"""Check that generalized fetch seeds add no input restrictions."""

import json
import shutil
from pathlib import Path

from tools.bbc.proofs import branch_induction, memory_composition
from tools.bbc.proofs.cpu_reference import documented_model, memory_interrupt_checks
from tools.bbc.proofs.video_cursor import hashes
from tools.validation.common import ROOT, write_json

TOP = "BbcCpuInterruptStreamContract"


def guard_seeds(graph):
    branch_induction.guard_domains(graph, TOP)
    module = graph["modules"][TOP]
    seen = set()
    for name, width in (("seedNmi", 1), ("seedPrevious", 1), ("seedOlderIrq", 5)):
        net = module["netnames"].get(name)
        if not net or "init" in net["attributes"]:
            raise ValueError("Missing or initialized fetch seed: " + name)
        bits = net["bits"]
        if len(bits) != width or any(type(b) is not int for b in bits) or seen.intersection(bits):
            raise ValueError("Constant, aliased or wrong-width fetch seed: " + name)
        drivers = [
            cell
            for cell in module["cells"].values()
            if any(
                direction == "output" and set(bits).intersection(cell["connections"][port])
                for port, direction in cell["port_directions"].items()
            )
        ]
        if (
            len(drivers) != 1
            or drivers[0]["type"] != "$anyconst"
            or drivers[0]["connections"]["Y"] != bits
        ):
            raise ValueError("Fetch seed is not an independent free constant: " + name)
        seen.update(bits)
    return {"seed_bits": len(seen), "seed_combinations": 1 << len(seen)}


def qualify():
    parent = ROOT / "build/bbc/cpu-c-control-induction-investigation/result.json"
    memory_composition.sealed(parent)
    work = Path("/tmp/fpgaleet-c-fetch-seed-domain")
    work.mkdir(exist_ok=True)
    prepared = Path("/tmp/fpgaleet-c-fetch-seed")
    graph, mapped = prepared / "graph.json", prepared / "mapped.json"
    preparation = prepared / "prepared.json"
    seal = json.loads(preparation.read_text())
    # This is an admission check of prepared inputs, not acceptance of that
    # preparation's still separate instruction-history result.
    if seal["outputs"] != hashes([prepared / "prepare.log", graph, mapped]):
        raise ValueError("Changed generalized-entry preparation")
    preparation_inputs = [
        Path(name) if Path(name).is_absolute() else ROOT / name for name in seal["inputs"]["files"]
    ]
    if seal["inputs"]["files"] != hashes(preparation_inputs):
        raise ValueError("Changed generalized-entry preparation inputs")
    inputs = [
        Path(__file__),
        *[
            Path(m.__file__)
            for m in (
                branch_induction,
                memory_composition,
                documented_model,
                memory_interrupt_checks,
            )
        ],
        Path(__file__).parents[1] / "video_cursor.py",
        ROOT / "tools/bbc/reference/official_stimulus.h",
        parent,
        graph,
        mapped,
        preparation,
        prepared / "prepare.log",
        prepared / "prefix.v",
        prepared / "contract.v",
        *preparation_inputs,
        Path(shutil.which("yosys")),
    ]
    before = hashes(inputs)
    domain = guard_seeds(json.loads(graph.read_text()))
    admission, artifacts = memory_interrupt_checks.admission(
        mapped, work, documented_model.inventory()
    )
    memory_composition.sealed(parent)
    stable = before == hashes(inputs)
    write_json(
        ROOT / "build/bbc/cpu-c-fetch-seed-domain/result.json",
        {
            "passed": stable,
            "inputs_unchanged": stable,
            "inputs": before,
            "artifacts": hashes(artifacts),
            "seed_admission": domain,
            "opcode_admission": admission,
            "scope": "Prepared generalized-entry graph has seven distinct, uninitialized, free anyconst seed bits admitting all 128 combinations. Its sole assumption depends only on the 16 opcode bits. Fresh combinational SAT, without applying that assumption, verifies all 65,536 opcode pairs against the independent 151-opcode inventory.",
            "limits": "Input admission only; neither prepared graphs nor this domain check constitute an instruction-history, reset/RTL handoff or whole-game verdict.",
            "universal_game_equivalence": False,
        },
    )
    print("Generalized fetch seed admission: " + ("PASS" if stable else "FAIL"), flush=True)
    if not stable:
        raise ValueError("Fetch seed domain inputs changed")


if __name__ == "__main__":
    qualify()
