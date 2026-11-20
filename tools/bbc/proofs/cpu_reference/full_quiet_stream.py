"""Run the quiet stream induction with the complete source decoder."""

import json
import shutil
import subprocess
from pathlib import Path

from tools.bbc.proofs import branch_induction, memory_composition
from tools.bbc.proofs.cpu_reference import prepared, quiet_stream
from tools.bbc.proofs.video_cursor import hashes
from tools.validation.common import ROOT, digest, write_json


def qualify():
    stream_premise = ROOT / "build/bbc/cpu-c-quiet-stream-bridge/result.json"
    full_premise = ROOT / "build/bbc/cpu-c-structural-pruning/result.json"
    stream = memory_composition.sealed(stream_premise)
    full = memory_composition.sealed(full_premise)
    model = Path("/tmp/fpgaleet-c-structural-pruning/pruned.v")
    source_work = Path("/tmp/fpgaleet-c-quiet-stream")
    prefix, contract = source_work / "prefix.v", source_work / "contract.v"
    for path, receipt in ((model, full), (prefix, stream), (contract, stream)):
        if receipt["artifacts"].get(str(path)) != digest(path):
            raise ValueError("Changed bound full-stream model")
    architecture = ROOT / "sim/formal/BbcCpuArchitectureContract.v"
    work = Path("/tmp/fpgaleet-c-full-quiet-stream")
    work.mkdir(exist_ok=True)
    directory = ROOT / "build/bbc/cpu-c-full-quiet-stream"
    directory.mkdir(exist_ok=True)
    files = [
        Path(__file__),
        Path(quiet_stream.__file__),
        Path(prepared.__file__),
        Path(branch_induction.__file__),
        Path(memory_composition.__file__),
        Path(__file__).parents[1] / "video_cursor.py",
        stream_premise,
        full_premise,
        model,
        prefix,
        contract,
        architecture,
        Path(shutil.which("yosys")),
    ]
    before = hashes(files)
    log, graph, mapped, witness = [
        work / n for n in ("proof.log", "graph.json", "mapped.json", "witness.json")
    ]
    witness.unlink(missing_ok=True)
    preparation = (
        f"read_verilog -formal -sv {model}; read_verilog -formal {prefix} {contract} {architecture}; "
        f"prep -top BbcCpuBranchStreamContract -flatten; check -assert; opt; chformal -lower; write_json {graph}; "
        f"techmap; opt; abc -g AND; opt_clean; write_json {mapped}"
    )
    cached = prepared.prepare(
        work, preparation, [model, prefix, contract, architecture], [graph, mapped]
    )
    command = (
        f"read_json {mapped}; "
        "sat -set-assumes -set-init-zero -tempinduct -initsteps 2 -maxsteps 8 -stepsize 1 "
        f"-prove equivalent 1 -verify -timeout 180 -show equivalent -show firstOpcode -show nextOpcode -dump_json {witness}"
    )
    with log.open("w") as output:
        run = subprocess.run(
            ["yosys", "-Q", "-T", "-p", command],
            stdout=output,
            stderr=subprocess.STDOUT,
            timeout=1200,
        )
    branch_induction.guard_domains(json.loads(graph.read_text()))
    branch_induction.guard_domains(json.loads(mapped.read_text()))
    stable = before == hashes(files)
    passed = stable and run.returncode == 0 and "Induction step proven: SUCCESS!" in log.read_text()
    write_json(
        directory / "result.json",
        {
            "passed": passed,
            "inputs_unchanged": stable,
            "inputs": before,
            "artifacts": hashes([p for p in work.iterdir() if p.is_file()]),
            "opcodes": stream["opcodes"],
            "preparation_cache_reused": cached,
            "scope": "Initialized induction of the unchanged quiet short/branch stream property with the complete pruned source C decoder: no opcode cases removed. All source state transitions and proved architectural/decoder/observer relations remain. The same single two-opcode assumption is guarded before and after ABC optimization; admission and actual source faults are separately sealed by the quiet-stream premise.",
            "limits": "33 two-cycle operations and eight branches, quiet reference fetch entry. C and structural translator trust, real OS startup, memory/control successors, interrupts, warm reset, clock enables, peripherals/video/reference phase and universal game equivalence remain open.",
            "universal_game_equivalence": False,
        },
    )
    print("Full decoder quiet stream induction: " + ("PASS" if passed else "FAIL"), flush=True)
    if not passed:
        raise ValueError("Full decoder quiet stream incomplete")


if __name__ == "__main__":
    qualify()
