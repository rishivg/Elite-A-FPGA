"""Reject both missing and extra opcodes in the universal admission check."""

import shutil
from pathlib import Path

from tools.bbc.proofs import branch_induction, memory_composition
from tools.bbc.proofs.cpu_reference import documented_model, memory_interrupt_checks
from tools.bbc.proofs.video_cursor import hashes
from tools.validation.common import ROOT, digest, write_json


def qualify():
    premise = ROOT / "build/bbc/cpu-c-documented-memory-investigation/result.json"
    parent = memory_composition.sealed(premise)
    graph = Path("/tmp/fpgaleet-c-documented-memory/mapped.json")
    if parent["artifacts"].get(str(graph)) != digest(graph):
        raise ValueError("Changed actual memory admission graph")
    inputs = [
        Path(__file__),
        Path(memory_interrupt_checks.__file__),
        Path(documented_model.__file__),
        Path(memory_composition.__file__),
        Path(__file__).parents[1] / "video_cursor.py",
        Path(branch_induction.__file__),
        ROOT / "tools/bbc/reference/official_stimulus.h",
        premise,
        graph,
        Path(shutil.which("yosys")),
    ]
    before = hashes(inputs)
    allowed = sorted(set(documented_model.inventory()) - memory_interrupt_checks.CONTROLS)
    work = Path("/tmp/fpgaleet-c-memory-domain-checks")
    work.mkdir(exist_ok=True)
    checks, artifacts = [], []
    for name, inventory in (
        ("positive", allowed),
        ("omitted-adc", [n for n in allowed if n != 0x65]),
        ("extra-php", sorted(allowed + [0x08])),
    ):
        case = work / name
        case.mkdir(exist_ok=True)
        try:
            _, files = memory_interrupt_checks.admission(graph, case, inventory)
        except ValueError:
            log = case / "admission.log"
            if (
                name == "positive"
                or not log.is_file()
                or "ERROR: Called with -verify and proof did fail!" not in log.read_text()
            ):
                raise
            files = [case / "admission.json", case / "admission.v", log]
        else:
            if name != "positive":
                raise ValueError("Wrong opcode inventory escaped detection")
        artifacts += files
        checks.append({"name": name, "passed": True})
        print(name + ": PASS", flush=True)
    stable = before == hashes(inputs)
    write_json(
        ROOT / "build/bbc/cpu-c-memory-domain-checks/result.json",
        {
            "passed": stable,
            "inputs_unchanged": stable,
            "inputs": before,
            "artifacts": hashes(artifacts),
            "checks": checks,
            "scope": "Fresh universal combinational admission equality for all 65536 opcode pairs, plus explicit SAT counterexamples for an omitted ADC and extra PHP inventory. The original domain assumption is observed, never applied to these queries.",
            "limits": "Admission checker controls only; no new CPU history or whole-game theorem.",
            "universal_game_equivalence": False,
        },
    )
    if not stable:
        raise ValueError("Admission inputs changed")


if __name__ == "__main__":
    qualify()
