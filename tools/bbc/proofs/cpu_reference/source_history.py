"""Run regenerated official CPU histories without temporary-directory premises."""

import argparse
import json
import shutil
import subprocess
from pathlib import Path

from tools.bbc.proofs import branch_induction, memory_composition
from tools.bbc.proofs.cpu_reference import (
    documented_model,
    fetch_seed_domain,
    foundation,
    frontend,
    history_recipe,
    memory_interrupt_checks,
    prepared,
    preservation_partitions,
    prune,
    short_stream,
    structural,
)
from tools.bbc.proofs.video_cursor import hashes
from tools.validation.common import ROOT, write_json

DIRECTORY = ROOT / "build/bbc/cpu-source-histories"
TOP = "BbcCpuInterruptStreamContract"


def prepare_models():
    DIRECTORY.mkdir(parents=True, exist_ok=True)
    # Preparation alone reports no verdict. It can run while the independent
    # translation foundation is still executing; qualify seals it separately.
    values = list(map(int, (foundation.DIRECTORY / "initial.log").read_text().split()))
    if len(values) != len(frontend.FIELDS) + 1:
        raise ValueError("Incomplete native constructor observation")
    initial = dict(zip([name for name, _ in frontend.FIELDS], values[:-1], strict=True))
    prefix, contract = history_recipe.generate(initial, values[-1])
    prefix_file, property_file = DIRECTORY / "prefix.v", DIRECTORY / "contract.v"
    prefix_file.write_text(prefix)
    property_file.write_text(contract)
    header = ROOT / "build/bbc/reference-cycle/m6502.h"
    expanded = subprocess.check_output(
        ["gcc", "-E", "-P", "-"], input=frontend.extract(header), text=True
    )
    model = DIRECTORY / "source.v"
    model.write_text(
        prune.prune(
            structural.generate(short_stream.adapter_ast(expanded, documented_model.inventory()))
        )[0]
    )
    full = foundation.DIRECTORY / "source.v"
    preservation = DIRECTORY / "preservation"
    preservation.mkdir(exist_ok=True)
    preservation_contract = preservation / "contract.v"
    preservation_contract.write_text(documented_model.contract(documented_model.inventory()))
    graph = preservation / "graph.json"
    prepared.prepare(
        preservation,
        f"read_verilog -formal -sv {full}; rename M6502ReferenceNext Full; read_verilog -formal -sv {model}; rename M6502ReferenceNext Reduced; read_verilog -formal {preservation_contract}; prep -top DocumentedModelContract -flatten; check -assert; opt; memory_map; opt; write_json {graph}",
        [full, model, preservation_contract],
        [graph],
        timeout=1800,
    )
    architecture = ROOT / "sim/formal/BbcCpuArchitectureContract.v"
    graph, mapped = DIRECTORY / "graph.json", DIRECTORY / "mapped.json"
    prepared.prepare(
        DIRECTORY,
        f"read_verilog -formal -sv {model}; read_verilog -formal {prefix_file} {property_file} {architecture}; prep -top {TOP} -flatten; check -assert; opt; memory_map; opt; chformal -lower; write_json {graph}; techmap; opt; abc -g AND; opt_clean; write_json {mapped}",
        [model, prefix_file, property_file, architecture],
        [graph, mapped],
        timeout=1800,
    )
    for path in (graph, mapped):
        branch_induction.guard_domains(json.loads(path.read_text()), TOP)
    fetch_seed_domain.guard_seeds(json.loads(graph.read_text()))
    return model, graph, mapped


def qualify(jobs=2):
    parent_path = foundation.DIRECTORY / "result.json"
    memory_composition.sealed(parent_path)
    files = [
        Path(__file__),
        parent_path,
        foundation.DIRECTORY / "source.v",
        foundation.DIRECTORY / "initial.log",
        ROOT / "build/bbc/reference-cycle/m6502.h",
        *history_recipe.producer_files(),
        *[
            Path(module.__file__)
            for module in (
                foundation,
                frontend,
                documented_model,
                fetch_seed_domain,
                memory_interrupt_checks,
                preservation_partitions,
                prepared,
                prune,
                short_stream,
                structural,
            )
        ],
        ROOT / "tools/validation/common.py",
        Path(branch_induction.__file__),
        Path(memory_composition.__file__),
        Path(__file__).parents[1] / "video_cursor.py",
        ROOT / "sim/formal/BbcCpuStreamPrefix.v",
        ROOT / "sim/formal/BbcCpuInterruptStreamContract.v",
        ROOT / "sim/formal/BbcCpuArchitectureContract.v",
        ROOT / "tools/bbc/reference/official_stimulus.h",
        Path(shutil.which("gcc")),
        Path(shutil.which("yosys")),
    ]
    before = hashes(files)
    _, graph, mapped = prepare_models()
    preservation = DIRECTORY / "preservation"
    preservation_result = preservation_partitions.qualify(
        preservation / "graph.json",
        preservation / "partitions",
        (ROOT / "build/bbc/reference-cycle/m6502.h").read_text(),
        jobs=jobs,
    )
    seeds = fetch_seed_domain.guard_seeds(json.loads(graph.read_text()))
    admission, _ = memory_interrupt_checks.admission(
        mapped, DIRECTORY, documented_model.inventory()
    )
    witness, log = DIRECTORY / "witness.json", DIRECTORY / "proof.log"
    witness.unlink(missing_ok=True)
    with log.open("w") as output:
        trial = subprocess.run(
            [
                "yosys",
                "-Q",
                "-T",
                "-p",
                f"read_json {mapped}; sat -set-assumes -set-init-zero -tempinduct -initsteps 14 -maxsteps 24 -prove equivalent 1 -verify -timeout 600 -show equivalent -show observedEquivalent -show stateRelation -show stage -dump_json {witness}",
            ],
            stdout=output,
            stderr=subprocess.STDOUT,
            timeout=7200,
        )
    memory_composition.sealed(parent_path)
    stable = before == hashes(files)
    passed = (
        stable and trial.returncode == 0 and "Induction step proven: SUCCESS!" in log.read_text()
    )
    # Other files in this directory may be exploration logs. Seal only the
    # maintained production's generated inputs, graphs and query artifacts.
    names = [
        "source.v",
        "prefix.v",
        "contract.v",
        "graph.json",
        "mapped.json",
        "prepared.json",
        "prepare.log",
        "admission.json",
        "admission.v",
        "admission.log",
        "proof.log",
    ]
    artifacts = [DIRECTORY / name for name in names]
    artifacts += [
        preservation / name for name in ("contract.v", "graph.json", "prepared.json", "prepare.log")
    ]
    artifacts += [path for path in (preservation / "partitions").rglob("*") if path.is_file()]
    if witness.is_file():
        artifacts.append(witness)
    write_json(
        DIRECTORY / "result.json",
        {
            "passed": passed,
            "inputs_unchanged": stable,
            "inputs": before,
            "artifacts": hashes(artifacts),
            "foundation": str(parent_path.relative_to(ROOT)),
            "opcode_admission": admission,
            "complete_source_preservation": preservation_result,
            "seed_domain": seeds,
            "opcodes": documented_model.inventory(),
            "scope": "Fresh initialized induction of the executing documented C decoder against the unchanged independent bus/architecture contract for all 151 official instructions with arbitrary IRQ/NMI and response bytes. Generalized reset-exit NMI/previous-pin/older-IRQ seeds are free. Complete-source preservation and all 65536 opcode-pair admission checks execute freshly. Existing maintained recipe regenerated from sources; no earlier result or missing /tmp artifact is used.",
            "limits": "Source history and generalized abstract entry. Its actual reset/fetch/retirement handoff, physical enable/NMI event mapping and RTL composition are separate. Existing actual-source fault controls require their own refreshed receipts. No whole-game equivalence claim.",
            "universal_game_equivalence": False,
        },
    )
    print(
        "Regenerated complete official source histories: " + ("PASS" if passed else "FAIL"),
        flush=True,
    )
    if not passed:
        raise ValueError("Source history induction incomplete")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--jobs", type=int, default=2)
    args = parser.parse_args()
    if args.prepare_only:
        prepare_models()
    else:
        qualify(jobs=args.jobs)
