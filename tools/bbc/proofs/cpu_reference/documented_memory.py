"""Use the preservation-proved official decoder and prove its execution context."""

import json
import shutil
import subprocess
from pathlib import Path

from tools.bbc.proofs import branch_induction, memory_composition
from tools.bbc.proofs.cpu_reference import documented_model, memory_interrupts, prepared
from tools.bbc.proofs.video_cursor import hashes, replace_once
from tools.validation.common import ROOT, digest, write_json


def qualify():
    premises = [
        ROOT / "build/bbc" / n / "result.json"
        for n in (
            "cpu-c-documented-model",
            "cpu-c-independent-translation",
            "cpu-c-zero-page-interrupt-bridge",
        )
    ]
    documented, _, _ = [memory_composition.sealed(p) for p in premises]
    model = Path("/tmp/fpgaleet-c-documented-model/source.v")
    if documented["artifacts"].get(str(model)) != digest(model):
        raise ValueError("Changed preservation-proved decoder")
    original_prefix = Path("/tmp/fpgaleet-c-protocol-invariants/prefix.v")
    original = ROOT / "sim/formal/BbcCpuInterruptStreamContract.v"
    architecture = ROOT / "sim/formal/BbcCpuArchitectureContract.v"
    work = Path("/tmp/fpgaleet-c-documented-memory")
    work.mkdir(exist_ok=True)
    destination = ROOT / "build/bbc/cpu-c-documented-memory-investigation/result.json"
    destination.parent.mkdir(exist_ok=True)
    files = [
        Path(__file__),
        *[
            Path(m.__file__)
            for m in (
                documented_model,
                memory_interrupts,
                prepared,
                branch_induction,
                memory_composition,
            )
        ],
        Path(__file__).parents[1] / "video_cursor.py",
        *premises,
        model,
        original_prefix,
        original,
        architecture,
        Path(shutil.which("yosys")),
    ]
    before = hashes(files)
    prefix = work / "prefix.v"
    text = replace_once(
        original_prefix.read_text(),
        "output [255:0] proofState",
        "output [63:0] proofResponse, output [255:0] proofState",
    )
    text = replace_once(
        text,
        "assign proofState=referenceState;",
        "assign proofResponse=response;\nassign proofState=referenceState;",
    )
    prefix.write_text(text)
    contract = work / "contract.v"
    text = memory_interrupts.strengthen(original.read_text())
    text = replace_once(
        text,
        "wire [255:0] cState,cHistory;",
        documented_model.context_function(documented["opcodes"])
        + "wire [63:0] cResponse;\nwire [255:0] cState,cHistory;",
    )
    text = replace_once(
        text, ".proofState(cState)", ".proofResponse(cResponse),.proofState(cState)"
    )
    text = replace_once(
        text,
        "(* keep *) wire stateRelation=",
        """(* keep *) wire sourceContext=(cResponse[24] && cResponse[28]) ||
 (cResponse[25] ? documented(cResponse[23:16]) :
 (cState[255:251]==0 && documented(cState[250:243])));
(* keep *) wire stateRelation=sourceContext &&
""",
    )
    contract.write_text(text)
    log, graph, mapped, witness = [
        work / n for n in ("proof.log", "graph.json", "mapped.json", "witness.json")
    ]
    witness.unlink(missing_ok=True)
    command = f"read_verilog -formal -sv {model}; read_verilog -formal {prefix} {contract} {architecture}; prep -top BbcCpuInterruptStreamContract -flatten; check -assert; opt; memory_map; opt; chformal -lower; write_json {graph}; techmap; opt; abc -g AND; opt_clean; write_json {mapped}"
    cached = prepared.prepare(
        work, command, [model, prefix, contract, architecture], [graph, mapped], timeout=1800
    )
    query = f"read_json {mapped}; sat -set-assumes -set-init-zero -tempinduct -initsteps 14 -maxsteps 20 -prove equivalent 1 -verify -timeout 600 -show equivalent -show observedEquivalent -show sourceContext -show stateRelation -show stage -show actualOpcode -show cState -show cHistory -dump_json {witness}"
    with log.open("w") as output:
        trial = subprocess.run(
            ["yosys", "-Q", "-T", "-p", query],
            stdout=output,
            stderr=subprocess.STDOUT,
            timeout=7200,
        )
    for path in (graph, mapped):
        branch_induction.guard_domains(
            json.loads(path.read_text()), "BbcCpuInterruptStreamContract"
        )
    stable = before == hashes(files)
    passed = (
        stable and trial.returncode == 0 and "Induction step proven: SUCCESS!" in log.read_text()
    )
    write_json(
        destination,
        {
            "passed": passed,
            "inputs_unchanged": stable,
            "inputs": before,
            "artifacts": hashes([p for p in work.iterdir() if p.is_file()]),
            "preparation_cache_reused": cached,
            "scope": "Initialized mixed official memory/short/branch/BRK source induction using the 151-opcode decoder whose output preservation is independently proved. The exact dispatch-context predicate is a proved state-relation conclusion at every executing tick, not an additional assumption. Original executing source state, bus and architecture property remain; only read-only response/context ports are added. Sole assumption restricts the two opcode slots.",
            "limits": "Unaccepted investigation until positive induction, admission and actual C faults pass. Abstract quiet fetch entry, nine other control successors, real enables/warm reset, OS and device/scheduler/video composition remain open.",
            "universal_game_equivalence": False,
        },
    )
    print("Documented-source memory histories: " + ("PASS" if passed else "FAIL"), flush=True)
    if not passed:
        raise ValueError("Documented-source memory history incomplete")


if __name__ == "__main__":
    qualify()
