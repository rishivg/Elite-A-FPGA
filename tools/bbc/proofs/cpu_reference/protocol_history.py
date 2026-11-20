"""Reproduce the source interrupt protocol induction from maintained inputs."""

import json
import re
import shutil
import subprocess
from pathlib import Path

from tools.bbc.proofs import branch_induction, memory_composition
from tools.bbc.proofs.cpu_reference import interrupt_stream, prepared, protocol_invariants
from tools.bbc.proofs.video_cursor import hashes, replace_once
from tools.validation.common import ROOT, digest, write_json


def qualify():
    cold = ROOT / "build/bbc/cpu-c-cold-entry-bridge/result.json"
    initialized = memory_composition.sealed(cold)
    profile = ROOT / "build/bbc/cpu-c-cold-entry-profile/result.json"
    memory_composition.sealed(profile)
    stream_premise = ROOT / "build/bbc/cpu-c-quiet-stream-bridge/result.json"
    stream = memory_composition.sealed(stream_premise)
    model = Path("/tmp/fpgaleet-c-quiet-stream/positive/source.v")
    if stream["artifacts"].get(str(model)) != digest(model):
        raise ValueError("Changed sealed source model")
    ordinary = ROOT / "sim/formal/BbcCpuStreamPrefix.v"
    original = ROOT / "sim/formal/BbcCpuInterruptStreamContract.v"
    architecture = ROOT / "sim/formal/BbcCpuArchitectureContract.v"
    # This is the same semantic preparation as the completed investigation.
    # Generate it directly: no old exploratory workspace or result is needed.
    work = Path("/tmp/fpgaleet-c-protocol-invariants")
    work.mkdir(exist_ok=True)
    directory = ROOT / "build/bbc/cpu-c-protocol-history"
    directory.mkdir(exist_ok=True)
    text = original.read_text()
    domains = re.findall(r"^always @\* assume\([^\n]+\);$", text, re.M)
    if len(domains) != 1:
        raise ValueError("Changed single source interrupt opcode domain")
    text = replace_once(
        text,
        domains[0],
        "always @* assume((firstOpcode==0 || firstOpcode==8'hea) && (nextOpcode==0 || nextOpcode==8'hea));",
    )
    contract = work / "contract.v"
    expected_contract = protocol_invariants.strengthen(text)
    prefix = work / "prefix.v"
    expected_prefix = interrupt_stream.prefix(
        initialized["default_native_state"],
        initialized["default_native_pins"],
        ordinary.read_text(),
    )
    expected_prefix = replace_once(
        expected_prefix,
        "output [255:0] proofState, output [63:0] proofPins, output proofTrap",
        "output [255:0] proofState, output [63:0] proofPins, output proofTrap, output [255:0] proofHistory, output proofPreviousFetch",
    )
    expected_prefix = replace_once(
        expected_prefix,
        "endmodule",
        "assign proofHistory=retirementState;assign proofPreviousFetch=previousFetch;\nendmodule",
    )
    for path, body in ((prefix, expected_prefix), (contract, expected_contract)):
        if not path.exists():
            path.write_text(body)
        elif path.read_text() != body:
            raise ValueError("Different existing protocol preparation: " + str(path))
    files = [
        Path(__file__),
        Path(interrupt_stream.__file__),
        Path(protocol_invariants.__file__),
        Path(prepared.__file__),
        Path(branch_induction.__file__),
        Path(memory_composition.__file__),
        Path(__file__).parents[1] / "video_cursor.py",
        cold,
        profile,
        stream_premise,
        model,
        ordinary,
        original,
        architecture,
        Path(shutil.which("yosys")),
    ]
    before = hashes(files)
    graph, mapped = work / "graph.json", work / "mapped.json"
    # Keep invocation results separate from immutable prepared models. This
    # permits independent SAT checks without overwriting another run's receipt.
    log, witness = directory / "proof.log", directory / "witness.json"
    witness.unlink(missing_ok=True)
    top = "BbcCpuInterruptStreamContract"
    preparation = (
        f"read_verilog -formal -sv {model}; read_verilog -formal {prefix} {contract} {architecture}; "
        f"prep -top {top} -flatten; check -assert; opt; chformal -lower; write_json {graph}; "
        f"techmap; opt; abc -g AND; opt_clean; write_json {mapped}"
    )
    cached = prepared.prepare(
        work, preparation, [model, prefix, contract, architecture], [graph, mapped]
    )
    query = (
        f"read_json {mapped}; sat -set-assumes -set-init-zero -tempinduct -initsteps 2 -maxsteps 12 "
        f"-prove equivalent 1 -verify -timeout 180 -show equivalent -show firstOpcode -show nextOpcode -dump_json {witness}"
    )
    with log.open("w") as output:
        run = subprocess.run(
            ["yosys", "-Q", "-T", "-p", query],
            stdout=output,
            stderr=subprocess.STDOUT,
            timeout=1200,
        )
    branch_induction.guard_domains(json.loads(graph.read_text()), top)
    branch_induction.guard_domains(json.loads(mapped.read_text()), top)
    stable = before == hashes(files)
    passed = stable and run.returncode == 0 and "Induction step proven: SUCCESS!" in log.read_text()
    write_json(
        directory / "result.json",
        {
            "passed": passed,
            "inputs_unchanged": stable,
            "inputs": before,
            "artifacts": hashes(
                [
                    prefix,
                    contract,
                    graph,
                    mapped,
                    work / "prepared.json",
                    work / "prepare.log",
                    log,
                    *([witness] if witness.exists() else []),
                ]
            ),
            "preparation_cache_reused": cached,
            "scope": "Initialized source NOP/BRK IRQ/NMI history induction regenerated directly from maintained contracts, pure read-only strengthening functions, sealed C source model and native cold-entry inputs. No exploratory result or pre-existing generated domain fixture is required. All executing source state drivers are retained; the sole two-opcode assumption is guarded before and after ABC.",
            "limits": "NOP/BRK and quiet reference fetch entry. Source faults/admission, complete decoder partition, broader instruction and physical enable/interrupt histories, custom C translation, actual OS/global phase and whole-game composition remain separate.",
            "universal_game_equivalence": False,
        },
    )
    print(
        "Reproducible source interrupt protocol induction: " + ("PASS" if passed else "FAIL"),
        flush=True,
    )
    if not passed:
        raise ValueError("Source protocol history proof incomplete")


if __name__ == "__main__":
    qualify()
