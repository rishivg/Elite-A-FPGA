"""Check the OS startup bridge rejects an actual stuck-high zero flag."""

import argparse
import json
import subprocess
from pathlib import Path

from tools.bbc import build
from tools.bbc.proofs import enable_composition
from tools.validation.common import ROOT, write_json


def qualify(waits=False):
    profile = "os-startup-zero-flag-waits" if waits else "os-startup-zero-flag-bridge"
    premise = build.BBC / profile / "result.json"
    enable_composition.sealed(premise)
    directory = build.BBC / (
        "os-startup-zero-flag-waits-fault" if waits else "os-startup-zero-flag-fault"
    )
    directory.mkdir(exist_ok=True)
    original = build.BBC / "os-startup-zero-flag-bridge/observed.json"
    contract = premise.parent / "contract.v"
    files = [Path(__file__), Path(enable_composition.__file__), premise, original, contract]
    before = build.hashes(files)
    graph = json.loads(original.read_text())
    actual = graph["modules"]["StartupActual"]
    zero = actual["ports"]["flags"]["bits"][1]
    drivers = [
        cell
        for cell in actual["cells"].values()
        if cell["type"] in {"$dff", "$adff"} and zero in cell["connections"]["Q"]
    ]
    if len(drivers) != 1 or drivers[0]["connections"]["Q"] != [zero]:
        raise ValueError("Changed actual zero-flag register")
    drivers[0]["connections"]["D"] = ["1"]
    netlist = directory / "stuck-zero.json"
    netlist.write_text(json.dumps(graph))
    witness = directory / "counterexample.json"
    command = f"read_json {netlist}; read_verilog -formal {contract}; prep -top StartupBridge -flatten; check -assert; async2sync; opt_clean; sat -set-init-zero -seq 24 -prove equivalent 1 -verify -show joined -show joining -show aAddress -show rAddress -dump_json {witness}"
    log = directory / "proof.log"
    with log.open("w") as out:
        result = subprocess.run(
            ["yosys", "-Q", "-T", "-p", command],
            cwd=ROOT,
            stdout=out,
            stderr=subprocess.STDOUT,
            timeout=240,
        )
    detected = (
        result.returncode != 0
        and "SAT proof finished - model found: FAIL!" in log.read_text()
        and witness.is_file()
    )
    if not detected:
        raise ValueError("Startup bridge accepted stuck-high zero flag")
    stable = before == build.hashes(files)
    enable_composition.sealed(premise)
    write_json(
        directory / "result.json",
        {
            "passed": stable,
            "inputs": before,
            "inputs_unchanged": stable,
            "artifacts": build.hashes([netlist, witness, log]),
            "actual_stuck_high_zero_flag_detected": detected,
            "arbitrary_disabled_edges": waits,
            "tools": {"yosys": subprocess.check_output(["yosys", "-V"], text=True).strip()},
            "scope": "An actual physical zero-flag D input is replaced with one. The unchanged, initialized OS startup contract must reject this mutated CPU by a bounded SAT witness. The reference netlist and all other actual next-state drivers remain unchanged.",
            "universal_game_equivalence": False,
        },
    )
    if not stable:
        raise ValueError("Startup fault inputs changed")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--waits", action="store_true", help="Check the arbitrary-wait bridge")
    qualify(waits=parser.parse_args().waits)
