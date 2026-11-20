"""Check observable faults in the regenerated official source-history contract."""

import argparse
import json
import shutil
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from tools.bbc.proofs import branch_induction, memory_composition
from tools.bbc.proofs.cpu_reference import (
    control_interrupt_checks,
    documented_model,
    frontend,
    memory_interrupt_checks,
    prepared,
    prune,
    short_stream,
    source_history,
    specialized,
    structural,
)
from tools.bbc.proofs.cpu_reference.warm_checks import accepted_result, observed_contract
from tools.bbc.proofs.video_cursor import hashes, replace_once
from tools.validation.common import ROOT, write_json

DIRECTORY = ROOT / "build/bbc/cpu-source-history-faults"


def guard_observer(original, observed):
    """Only expose an existing trap output and add read-only propositions."""
    restored = replace_once(
        observed,
        "(* keep *) wire cTrap;\nwire [255:0] cState,cHistory;",
        "wire [255:0] cState,cHistory;",
    )
    restored = replace_once(
        restored, ".proofTrap(cTrap),.proofResponse(cResponse)", ".proofResponse(cResponse)"
    )
    restored = replace_once(
        restored,
        "(* keep *) wire definedObserved=reset || (!cTrap && sourceContext && observedEquivalent);\n(* keep *) wire faultObservable=reset || cTrap || !sourceContext || observedEquivalent;\n",
        "",
    )
    if restored != original:
        raise ValueError("Fault observer changed the proved source contract")


def prove_positive_implication(graph, directory):
    """Transfer the sealed unbounded theorem to the stronger positive view."""
    directory.mkdir(exist_ok=True)
    top = source_history.TOP
    design = json.loads(graph.read_text())
    module = design["modules"][top]
    module["ports"]["definedObserved"] = {
        "direction": "output",
        "bits": module["netnames"]["definedObserved"]["bits"],
    }
    # The implication must hold for arbitrary storage, not just construction.
    # This is a reference-only projection; no actual next-state driver changes.
    for net in module["netnames"].values():
        net["attributes"].pop("init", None)
    core, wrapper, mapped, log = [
        directory / name for name in ("core.json", "contract.v", "graph.json", "proof.log")
    ]
    core.write_text(json.dumps(design))
    wrapper.write_text(
        "module SourcePositiveImplication(input clk,output implication);\nwire equivalent,definedObserved;\n"
        + top
        + " original(.clk(clk),.equivalent(equivalent),.definedObserved(definedObserved));\nassign implication=!equivalent || definedObserved;\nendmodule\n"
    )
    prepared.prepare(
        directory,
        f"read_json {core}; read_verilog -formal {wrapper}; prep -top SourcePositiveImplication -flatten; check -assert; opt; memory_map; opt; write_json {mapped}",
        [core, wrapper],
        [mapped],
    )
    with log.open("w") as output:
        trial = subprocess.run(
            [
                "yosys",
                "-Q",
                "-T",
                "-p",
                f"read_json {mapped}; sat -seq 1 -prove implication 1 -verify -timeout 60",
            ],
            stdout=output,
            stderr=subprocess.STDOUT,
            timeout=180,
        )
    if trial.returncode or "SAT proof finished - no model found: SUCCESS!" not in log.read_text():
        raise ValueError("Unbounded source theorem does not entail the positive observation")
    return {
        "passed": True,
        "state_initialization_removed": True,
        "assumptions_applied": False,
        "domain": "Arbitrary reference storage and all symbolic inputs. The unchanged sealed unbounded source theorem implies definedObserved; the fixed fault stimuli are admitted subsets of its official opcode domain.",
    }


def cases(source):
    result = []
    for name, opcode, old, new, step in memory_interrupt_checks.FAULTS:
        line = next(
            line for line in source.splitlines() if f"case (0x{opcode:02X}<<3)|{step}: " in line
        )
        result.append(
            (name, opcode, replace_once(source, line, replace_once(line, old, new)), True)
        )
    for name, opcode, step, old, new in control_interrupt_checks.FAULTS:
        line = next(
            line for line in source.splitlines() if f"case (0x{opcode:02X}<<3)|{step}: " in line
        )
        result.append(
            (name, opcode, replace_once(source, line, replace_once(line, old, new)), True)
        )
    for name, opcode, old, new in (
        ("lost-nmi-edge", 0xEA, "c->nmi_pip |= 0x100;", "c->nmi_pip |= 0;"),
        (
            "lost-branch-irq-restoration",
            0x10,
            "case (0x10<<3)|3: c->PC=c->AD;if(c->branch_irq)c->irq_pip|=0x200;",
            "case (0x10<<3)|3: c->PC=c->AD;",
        ),
    ):
        result.append((name, opcode, replace_once(source, old, new), False))
    return result


def qualify(jobs=2):
    if not 1 <= jobs <= 19:
        raise ValueError("Source fault workers must be between 1 and 19")
    started = time.monotonic()
    parent_path = source_history.DIRECTORY / "result.json"
    history = memory_composition.sealed(parent_path)
    if history.get("opcodes") != documented_model.inventory():
        raise ValueError("Positive history has a different official opcode domain")
    DIRECTORY.mkdir(parents=True, exist_ok=True)
    source = ROOT / "build/bbc/reference-cycle/m6502.h"
    prefix = source_history.DIRECTORY / "prefix.v"
    original_contract = source_history.DIRECTORY / "contract.v"
    original_model = source_history.DIRECTORY / "source.v"
    architecture = ROOT / "sim/formal/BbcCpuArchitectureContract.v"
    files = [
        Path(__file__),
        parent_path,
        source,
        prefix,
        original_contract,
        original_model,
        architecture,
        *[
            Path(module.__file__)
            for module in (
                control_interrupt_checks,
                documented_model,
                frontend,
                memory_interrupt_checks,
                prepared,
                prune,
                short_stream,
                source_history,
                specialized,
                structural,
            )
        ],
        Path(__file__).with_name("warm_checks.py"),
        ROOT / "tools/validation/common.py",
        Path(branch_induction.__file__),
        Path(memory_composition.__file__),
        Path(__file__).parents[1] / "video_cursor.py",
        *[Path(shutil.which(tool)) for tool in ("gcc", "yosys")],
    ]
    before = hashes(files)
    property_file = DIRECTORY / "contract.v"
    text = observed_contract(original_contract.read_text())
    text = replace_once(
        text,
        "reset || cTrap || observedEquivalent",
        "reset || cTrap || !sourceContext || observedEquivalent",
    )
    guard_observer(original_contract.read_text(), text)
    property_file.write_text(text)
    top = source_history.TOP

    def prepare_case(case, model):
        graph = case / "graph.json"
        prepared.prepare(
            case,
            f"read_verilog -formal -sv {model}; read_verilog -formal {prefix} {property_file} {architecture}; prep -top {top} -flatten; check -assert; opt; memory_map; opt; chformal -lower; write_json {graph}; techmap; opt; abc -g AND; opt_clean; write_json {case}/mapped.json",
            [model, prefix, property_file, architecture],
            [graph, case / "mapped.json"],
            timeout=1800,
        )
        branch_induction.guard_domains(json.loads(graph.read_text()), top)
        return case / "mapped.json"

    positive = DIRECTORY / "positive"
    positive.mkdir(exist_ok=True)
    prepare_case(positive, original_model)
    positive_view = prove_positive_implication(
        positive / "graph.json", DIRECTORY / "positive-implication"
    )
    mutations = cases(source.read_text())
    if (
        any(opcode not in history["opcodes"] for _, opcode, _, _ in mutations)
        or 234 not in history["opcodes"]
    ):
        raise ValueError("Fault stimulus lies outside the unbounded positive theorem")

    def check_case(item):
        name, opcode, changed, quiet = item
        case = DIRECTORY / name
        case.mkdir(exist_ok=True)
        header = case / "reference.h"
        header.write_text(changed)
        expanded = subprocess.check_output(
            ["gcc", "-E", "-P", "-"], input=frontend.extract(header), text=True
        )
        model = case / "source.v"
        model.write_text(
            prune.prune(
                structural.generate(
                    short_stream.adapter_ast(expanded, documented_model.inventory())
                )
            )[0]
        )
        mutated_graph = prepare_case(case, model)
        stimulus = f"-set firstOpcode {opcode} -set nextOpcode 234"
        if quiet:
            stimulus += " -set irqPin 0 -set nmiPin 0"
        bindings = {"firstOpcode": opcode, "nextOpcode": 234}
        if quiet:
            bindings.update(irqPin=0, nmiPin=0)
        if name == "wrong-absolute-y-address":
            # Y=0 makes this address mutation inert. The positive theorem is
            # universal in Y; select a nonzero index for this negative probe.
            bindings["y"] = 1
        outcomes = [
            {
                "name": "positive",
                "accepted": True,
                "basis": "Fresh universal implication of the unchanged sealed unbounded source history",
            }
        ]
        for label, graph, prop, correct in (("fault", mutated_graph, "faultObservable", False),):
            specialization = case / label
            specialization.mkdir(exist_ok=True)
            fixed, reduced = specialization / "fixed.json", specialization / "graph.json"
            fixed.write_text(
                json.dumps(specialized.substitute(json.loads(graph.read_text()), top, bindings))
            )
            prepared.prepare(
                specialization,
                f"read_json {fixed}; hierarchy -top {top}; opt; opt_clean; write_json {reduced}",
                [fixed],
                [reduced],
            )
            specialized.check_constants(json.loads(reduced.read_text()), top, bindings)
            log, witness = case / (label + ".log"), case / (label + "-witness.json")
            witness.unlink(missing_ok=True)
            with log.open("w") as output:
                trial = subprocess.run(
                    [
                        "yosys",
                        "-Q",
                        "-T",
                        "-p",
                        f"read_json {reduced}; sat -set-assumes -set-init-zero -seq 24 -prove {prop} 1 -verify {stimulus} -timeout 300 -show {prop} -show cTrap -show sourceContext -dump_json {witness}",
                    ],
                    stdout=output,
                    stderr=subprocess.STDOUT,
                    timeout=900,
                )
            outcomes.append(
                {
                    "name": label,
                    "accepted": accepted_result(
                        trial.returncode, log.read_text(), witness, prop, correct
                    ),
                }
            )
        accepted = all(check["accepted"] for check in outcomes)
        return {"name": name, "accepted": accepted, "checks": outcomes, "stimulus": bindings}

    checks = []
    with ThreadPoolExecutor(max_workers=jobs) as pool:
        for check in pool.map(check_case, mutations):
            checks.append(check)
            print(
                "Source history fault "
                + check["name"]
                + (": PASS" if check["accepted"] else ": FAIL"),
                flush=True,
            )
            write_json(
                DIRECTORY / "progress.json", {"checks": checks, "universal_game_equivalence": False}
            )
    memory_composition.sealed(parent_path)
    stable = before == hashes(files)
    passed = stable and len(checks) == 19 and all(check["accepted"] for check in checks)
    artifacts = [
        property_file,
        *[path for path in (DIRECTORY / "positive-implication").glob("*") if path.is_file()],
        *[
            p
            for directory in (positive, *[DIRECTORY / check["name"] for check in checks])
            for p in directory.rglob("*")
            if p.is_file()
        ],
    ]
    write_json(
        DIRECTORY / "result.json",
        {
            "passed": passed,
            "workers": jobs,
            "wall_seconds": time.monotonic() - started,
            "inputs_unchanged": stable,
            "inputs": before,
            "artifacts": hashes(artifacts),
            "checks": checks,
            "positive_observation": positive_view,
            "scope": "Nineteen actual maintained-C mutations in memory addressing, writes, stack/jumps, NMI edge sampling and branch IRQ restoration must fail an observable bus/retirement property on a defined source history at 24 ticks. A fresh all-state implication transfers the sealed unbounded unchanged-source theorem to definedObserved. Exact observer restoration guards every executing driver/property byte, and each fixed fault stimulus is an admitted subset of the parent domain. All 151 official opcodes and generalized entry seeds remain admitted; repeated bounded positive queries are replaced by the stronger positive theorem.",
            "limits": "Fault sensitivity within the regenerated source history. Does not establish physical-enable mapping, warm history or RTL/whole-machine equivalence.",
            "universal_game_equivalence": False,
        },
    )
    if not passed:
        raise ValueError("Source history fault checks incomplete")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--jobs", type=int, default=2)
    args = parser.parse_args()
    qualify(jobs=args.jobs)
