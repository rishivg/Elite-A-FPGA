"""Require actual C faults to fail both strengthened instruction boundaries."""

import json
import shutil
import subprocess
from pathlib import Path

from tools.bbc import cpu_instruction_proof
from tools.bbc.proofs import memory_composition
from tools.bbc.proofs.cpu_reference import eligible_instructions, fetch_boundaries
from tools.bbc.proofs.video_cursor import hashes
from tools.validation.common import ROOT, write_json


def qualify():
    paths = [
        ROOT / "build/bbc" / name / "result.json"
        for name in (
            "cpu-c-instruction-bridge-faults",
            "cpu-c-eligible-instruction-bridge",
            "cpu-c-fetch-boundaries",
        )
    ]
    faults, eligible, boundaries = [memory_composition.sealed(p) for p in paths]
    if not all(r.get("complete_official_domain") for r in (eligible, boundaries)):
        raise ValueError("Both complete 151-opcode boundary proofs are required")
    for record in faults["faults"]:
        if not record["detected"] or record["artifacts"] != hashes(
            [Path(n) if Path(n).is_absolute() else ROOT / n for n in record["artifacts"]]
        ):
            raise ValueError("Changed actual source fault: " + record["name"])
    work = Path("/tmp/fpgaleet-c-boundary-faults")
    work.mkdir(exist_ok=True)
    directory = ROOT / "build/bbc/cpu-c-boundary-faults"
    directory.mkdir(exist_ok=True)
    architecture = ROOT / "sim/formal/BbcCpuArchitectureContract.v"
    files = [
        Path(__file__),
        Path(eligible_instructions.__file__),
        Path(fetch_boundaries.__file__),
        Path(cpu_instruction_proof.__file__),
        Path(memory_composition.__file__),
        Path(__file__).parents[1] / "video_cursor.py",
        *paths,
        architecture,
        Path(shutil.which("yosys")),
        *[
            Path(n) if Path(n).is_absolute() else ROOT / n
            for record in faults["faults"]
            for n in record["artifacts"]
        ],
    ]
    before = hashes(files)
    results, artifacts = [], []
    for boundary, builder, depth in (
        ("eligible", eligible_instructions.prefix, 17),
        ("next-fetch", fetch_boundaries.prefix, 10),
    ):
        root = work / boundary
        root.mkdir(exist_ok=True)
        prefix = root / "prefix.v"
        prefix.write_text(builder())
        artifacts.append(prefix)
        for fault in faults["faults"]:
            original = Path("/tmp/fpgaleet-c-instruction-bridge-faults") / fault["name"]
            model, old_contract = original / "fault.v", original / "contract.v"
            contract_text = old_contract.read_text()
            if boundary == "next-fetch":
                top, contract_text = fetch_boundaries.contract(contract_text)
            else:
                # This parser also validates the exact contract interface.
                top, _ = fetch_boundaries.contract(contract_text)
            case = root / fault["name"]
            case.mkdir(exist_ok=True)
            contract = case / "contract.v"
            contract.write_text(contract_text)
            log, graph, witness = [case / n for n in ("proof.log", "graph.json", "witness.json")]
            witness.unlink(missing_ok=True)
            command = (
                f"read_verilog -formal -sv {model}; read_verilog -formal {contract} {prefix} {architecture}; "
                f"prep -top {top} -flatten; check -assert; opt; chformal -lower; write_json {graph}; "
                f"sat -set-assumes -set-init-zero -seq {depth} -set opcode {int(fault['opcode'], 16)} "
                f"-prove equivalent 1 -verify -timeout 120 -show equivalent -show step -dump_json {witness}"
            )
            with log.open("w") as output:
                run = subprocess.run(
                    ["yosys", "-Q", "-T", "-p", command],
                    stdout=output,
                    stderr=subprocess.STDOUT,
                    timeout=300,
                )
            cpu_instruction_proof.guard_assumptions(json.loads(graph.read_text()), top)
            detected = (
                run.returncode != 0
                and "ERROR: Called with -verify and proof did fail!" in log.read_text()
                and witness.is_file()
                and any(
                    s["name"] == "equivalent" and "0" in s.get("wave", "")
                    for s in json.loads(witness.read_text())["signal"]
                )
            )
            artifacts += [contract, log, graph, *([witness] if witness.exists() else [])]
            results.append({"boundary": boundary, "name": fault["name"], "detected": detected})
            print(boundary + " " + fault["name"] + (": PASS" if detected else ": FAIL"), flush=True)
            if not detected:
                raise ValueError("Boundary fault must produce an initialized counterexample")
    stable = before == hashes(files)
    write_json(
        directory / "result.json",
        {
            "passed": stable,
            "inputs_unchanged": stable,
            "inputs": before,
            "artifacts": hashes(artifacts),
            "faults": results,
            "scope": "Three sealed actual C decoder mutations checked independently against both complete strengthened instruction-unit interfaces. Each of the six queries requires an initialized SAT equivalence counterexample; timeout or tool errors are rejected. Original opcode-only assumption cones are checked.",
            "limits": "Sensitivity checks for bounded quiet instruction and next-fetch proofs. Compiler trust, complete-decoder specialization, arbitrary streams, interrupts and whole-machine equivalence remain separate.",
            "universal_game_equivalence": False,
        },
    )
    if not stable:
        raise ValueError("Boundary fault inputs changed")


if __name__ == "__main__":
    qualify()
