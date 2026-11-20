"""Run the NOP/BRK interrupt induction with the complete source C decoder."""

import json
import shutil
import subprocess
from pathlib import Path

from tools.bbc.proofs import branch_induction, memory_composition
from tools.bbc.proofs.cpu_reference import prepared, protocol_history
from tools.bbc.proofs.video_cursor import hashes
from tools.validation.common import ROOT, digest, write_json


def qualify():
    premise = ROOT / "build/bbc/cpu-c-protocol-history/result.json"
    full_premise = ROOT / "build/bbc/cpu-c-structural-pruning/result.json"
    protocol, full = [memory_composition.sealed(p) for p in (premise, full_premise)]
    model = Path("/tmp/fpgaleet-c-structural-pruning/pruned.v")
    original = Path("/tmp/fpgaleet-c-protocol-invariants")
    prefix, contract = original / "prefix.v", original / "contract.v"
    for path, bound in ((model, full), (prefix, protocol), (contract, protocol)):
        if bound["artifacts"].get(str(path)) != digest(path):
            raise ValueError("Changed bound full-protocol model")
    architecture = ROOT / "sim/formal/BbcCpuArchitectureContract.v"
    work = Path("/tmp/fpgaleet-c-full-protocol")
    work.mkdir(exist_ok=True)
    directory = ROOT / "build/bbc/cpu-c-full-protocol"
    directory.mkdir(exist_ok=True)
    files = [
        Path(__file__),
        Path(protocol_history.__file__),
        Path(prepared.__file__),
        Path(branch_induction.__file__),
        Path(memory_composition.__file__),
        Path(__file__).parents[1] / "video_cursor.py",
        premise,
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
    top = "BbcCpuInterruptStreamContract"
    preparation = (
        f"read_verilog -formal -sv {model}; read_verilog -formal {prefix} {contract} {architecture}; "
        f"prep -top {top} -flatten; check -assert; opt; chformal -lower; write_json {graph}; "
        f"techmap; opt; abc -g AND; opt_clean; write_json {mapped}"
    )
    cached = prepared.prepare(
        work, preparation, [model, prefix, contract, architecture], [graph, mapped], timeout=1800
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
            timeout=1800,
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
            "artifacts": hashes([p for p in work.iterdir() if p.is_file()]),
            "preparation_cache_reused": cached,
            "scope": "Initialized NOP/BRK IRQ/NMI history induction with the complete pruned source C decoder, no opcode cases removed. All meaningful architecture, stack/vector bytes, PCs and subsequent interrupt pins remain symbolic. Unchanged protocol bus/architecture property and read-only strengthened state relations; the only assumption is its two-opcode input domain, guarded before and after ABC.",
            "limits": "NOP/BRK domain, abstract quiet fetch entry and custom C translation. Admission/source faults are independently checked by the protocol bridge; this run does not inherit their result. Arithmetic/branch/control/memory successors, cold/OS entry composition, physical enable/interrupt histories, warm reset and whole-game equivalence remain separate.",
            "universal_game_equivalence": False,
        },
    )
    print(
        "Full decoder interrupt protocol induction: " + ("PASS" if passed else "FAIL"), flush=True
    )
    if not passed:
        raise ValueError("Full source interrupt protocol induction incomplete")


if __name__ == "__main__":
    qualify()
