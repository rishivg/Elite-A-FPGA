"""Extend the exact released-OS startup bridge to arbitrary disabled CPU edges."""

import re
import subprocess
from pathlib import Path

from tools.bbc import build
from tools.bbc.proofs import enable_composition
from tools.validation.common import ROOT, write_json


def qualify():
    premise = build.BBC / "os-startup-zero-flag-bridge/result.json"
    enable_composition.sealed(premise)
    netlist = premise.parent / "observed.json"
    original = premise.parent / "contract.v"
    files = [Path(__file__), Path(enable_composition.__file__), premise, netlist, original]
    before = build.hashes(files)
    text = original.read_text()
    replacements = {
        "(* anyseq *)reg warm;": "(* anyseq *)reg warm,enable;",
        ".enable(1'b1)": ".enable(enable)",
        "else begin\n  if(joining)": "else if(enable) begin\n  if(joining)",
        "wire prejoinDecode=": "wire prejoinRead=phase==22 ? readData==8'hcd : phase==23 ? readData==8'hd9 : phase==13 ? readData==8'h40 : phase==12 && !warmSecond && !joining ? readData==8'ha9 : 1'b1;\nwire prejoinDecode=",
        "prejoinRegisters && prejoinDecode": "prejoinRegisters && prejoinRead && prejoinDecode",
    }
    for old, new in replacements.items():
        expected = 2 if old == ".enable(1'b1)" else 1
        if text.count(old) != expected:
            raise ValueError("Changed startup bridge anchor: " + old)
        text = text.replace(old, new)
    directory = build.BBC / "os-startup-zero-flag-waits"
    directory.mkdir(exist_ok=True)
    contract = directory / "contract.v"
    contract.write_text(text)
    graph = directory / "graph.json"
    witness = directory / "counterexample.json"
    command = f"read_json {netlist}; read_verilog -formal {contract}; prep -top StartupBridge -flatten; check -assert; async2sync; opt_clean; write_json {graph}; sat -set-init-zero -tempinduct -initsteps 1 -maxsteps 24 -prove equivalent 1 -verify -timeout 120 -show enable -show phase -show aAddress -show rAddress -dump_json {witness}"
    log = directory / "proof.log"
    with log.open("w") as out:
        result = subprocess.run(
            ["yosys", "-Q", "-T", "-p", command],
            cwd=ROOT,
            stdout=out,
            stderr=subprocess.STDOUT,
            timeout=1800,
        )
    proof = log.read_text()
    if result.returncode != 0 or "Induction step proven: SUCCESS!" not in proof:
        raise ValueError("Physical startup wait bridge failed")
    stable = before == build.hashes(files)
    enable_composition.sealed(premise)
    write_json(
        directory / "result.json",
        {
            "passed": stable,
            "inputs": before,
            "inputs_unchanged": stable,
            "artifacts": build.hashes([contract, graph, log]),
            "register_bits": 168,
            "excluded_register_bits": 0,
            "induction_depth": int(re.findall(r"Trying induction with length (\d+)", proof)[-1]),
            "tools": {"yosys": subprocess.check_output(["yosys", "-V"], text=True).strip()},
            "scope": "The source-identical physical CPU pair from the sealed enable-one OS bridge, with arbitrary inserted disabled master edges and arbitrary warm requests. ROM responses advance only at acceptance. A proved startup response-byte invariant strengthens induction; no new input assumption. Bus observations and writes agree, and all 168 physical state bits converge after the first LDA retirement edge and before any write.",
            "limits": "Verified OS startup bytes, specified simulation initialization, inactive IRQ/NMI and no subsequent cold reset. Parent ROM response contents/timing and subsequent instruction streams retain their separate contracts; no independent full CPU or universal game claim.",
            "universal_game_equivalence": False,
        },
    )
    if not stable:
        raise ValueError("Startup wait inputs changed")


if __name__ == "__main__":
    qualify()
