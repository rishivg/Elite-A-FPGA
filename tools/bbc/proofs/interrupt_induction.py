"""Prove instruction streams with unrestricted IRQ/NMI after ordinary initialization."""

import argparse
import json
import subprocess
from pathlib import Path

from tools.bbc import build, cpu_instruction_proof, cpu_interrupt_matrix, cpu_interrupt_proof
from tools.bbc.proofs.branch_induction import guard_domains
from tools.validation.common import ROOT, write_json

TOP = "BbcCpuInterruptStreamContract"


def independent_support():
    path = build.BBC / "cpu-interrupt-matrix/result.json"
    try:
        report = cpu_instruction_proof.supporting_evidence(path)
    except (OSError, ValueError, KeyError):
        cpu_interrupt_matrix.qualify()
        report = cpu_instruction_proof.supporting_evidence(path)
    return path, report


def qualify(all_documented=False, irq_only=False):
    if not build.generation_current():
        raise ValueError("Generate the current CPU before proving interrupt streams")
    name = (
        "cpu-documented-irq-stream-proof"
        if all_documented and irq_only
        else "cpu-documented-interrupt-stream-proof"
        if all_documented
        else "cpu-interrupt-stream-proof"
    )
    directory = build.BBC / name
    directory.mkdir(exist_ok=True)
    formal = ROOT / "sim/formal"
    core = build.BBC / "ArletCpuEnabled.v"
    common = [
        ROOT / "rtl/bbc/BbcCpu.v",
        ROOT / "third_party/6502/ALU.v",
        formal / "BbcCpuInterruptStreamPrefix.v",
        formal / "BbcCpuArchitectureContract.v",
        formal / (TOP + ".v"),
    ]
    ordinary = formal / "BbcCpuStreamPrefix.v"
    expected = (
        ordinary.read_text()
        .replace("BbcCpuStreamPrefix", "BbcCpuInterruptStreamPrefix")
        .replace(
            "module BbcCpuInterruptStreamPrefix(",
            "module BbcCpuInterruptStreamPrefix #(parameter ALLOW_NMI=1)(",
        )
        .replace("input clk,", "input clk, input irqPin, input nmiPin,")
        .replace(".irq(1'b0)", ".irq(age>=26 ? irqPin : 1'b0)")
        .replace(".nmi(1'b0)", ".nmi(age>=26 && ALLOW_NMI ? nmiPin : 1'b0)")
    )
    if common[2].read_text() != expected:
        raise ValueError("Interrupt prefix changed beyond its declared pin wiring")
    inventory = ROOT / "tools/bbc/reference/official_stimulus.h"
    memory_contract = formal / "BbcCpuMemoryContract.v"
    groups = cpu_instruction_proof.memory_groups(memory_contract.read_text(), inventory.read_text())
    opcodes = sorted(
        {
            *cpu_instruction_proof.SCALAR_OPCODES,
            *(cpu_instruction_proof.CONTROL_OPCODES if all_documented else ()),
            *([op for _, ops, _ in groups for op in ops] if all_documented else ()),
        }
    )
    if len(opcodes) != (151 if all_documented else 42):
        raise ValueError("Unexpected interrupt instruction domain")
    support, reference = independent_support()
    inputs = [Path(__file__), core, *common, ordinary, inventory, memory_contract, support]
    for group in ("inputs", "artifacts"):
        inputs.extend(ROOT / name for name in reference[group])
    inputs.extend([Path(cpu_interrupt_proof.__file__), Path(cpu_instruction_proof.__file__)])
    before = build.hashes(inputs)
    faults = [
        (
            "old-decimal",
            "adc_bcd <= (plp ? ADD[3] : cld ? 1'b0 : sed ? 1'b1 : D) && state != BRK0;",
            "adc_bcd <= D;",
        ),
        ("inverted-bpl", "3'b000: cond_true = ~N;", "3'b000: cond_true = N;"),
        (
            "wrong-hardware-b",
            "BRK2:    DO = bbc_hardware_interrupt ? (P & 8'b1110_1111) : P;",
            "BRK2:    DO = P;",
        ),
        *(() if irq_only else cpu_interrupt_proof.FAULTS),
    ]
    variants = [("positive", core)]
    artifacts = []
    actual = core.read_text()
    for label, correct, wrong in faults:
        model = directory / (label + ".v")
        model.write_text(cpu_instruction_proof.mutated(actual, correct, wrong))
        artifacts.append(model)
        variants.append((label, model))
    graph = directory / "positive.json"
    results = []
    depth = 72 if all_documented else 56
    maximum = 88 if all_documented else 76
    for label, model in variants:
        log = directory / (label + ".log")
        script = directory / (label + ".ys")
        wave = directory / (label + "-counterexample.json")
        command = (
            "read_verilog -formal "
            + " ".join(map(str, [model, *common]))
            + f"; hierarchy -top {TOP} -chparam ALL_DOCUMENTED {int(all_documented)} -chparam ALLOW_NMI {int(not irq_only)}; "
            + f"prep -top {TOP} -flatten; check -assert; memory_map; opt; async2sync; opt_clean; "
            + (f"write_json {graph}; " if label == "positive" else "")
            + (
                f"sat -set-assumes -set-init-zero -tempinduct -initsteps {depth} -maxsteps {maximum} -stepsize 4"
                if label == "positive"
                else "sat -set-assumes -set-init-zero -tempinduct-baseonly -maxsteps 56"
            )
            + f" -prove equivalent 1 -verify -timeout 300 -dump_json {wave}"
        )
        script.write_text(command + "\n")
        with log.open("w") as output:
            run = subprocess.run(
                ["yosys", "-Q", "-T", "-s", str(script)],
                stdout=output,
                stderr=subprocess.STDOUT,
                cwd=ROOT,
                timeout=1800,
            )
        text = log.read_text()
        structural = not any(
            problem in text
            for problem in (
                "multiple conflicting drivers",
                "Driver-driver conflict",
                "has no driver",
            )
        )
        accepted = structural and (
            run.returncode == 0 and "Induction step proven: SUCCESS!" in text
            if label == "positive"
            else run.returncode != 0 and "model found for base case: FAIL!" in text
        )
        results.append({"name": label, "accepted": accepted, "returncode": run.returncode})
        artifacts.extend([script, log])
        if wave.is_file():
            artifacts.append(wave)
        print(results[-1], flush=True)
    guard_domains(json.loads(graph.read_text()), TOP)
    module = json.loads(graph.read_text())["modules"][TOP]
    for pin in ("irqPin", *(() if irq_only else ("nmiPin",))):
        bits = module["netnames"][pin]["bits"]
        if len(bits) != 1 or not any(
            cell["type"] == "$anyseq" and cell["connections"].get("Y") == bits
            for cell in module["cells"].values()
        ):
            raise ValueError("Interrupt pin is not freely changing: " + pin)
    excluded = [op for op in range(256) if op not in opcodes]
    domain_graph = directory / "domains.json"
    domain_script = directory / "domains.ys"
    commands = [
        f"sat -set-assumes -set-init-zero -seq 1 -set firstOpcode {a} -set nextOpcode {b} -show firstOpcode -show nextOpcode"
        for a in opcodes
        for b in opcodes
    ]
    commands.extend(
        f"sat -set-assumes -set-init-zero -seq 1 -set {signal} {op} -show {signal}"
        for signal in ("firstOpcode", "nextOpcode")
        for op in excluded
    )
    domain_script.write_text(
        f"read_json {graph}; delete -output {TOP}/w:equivalent; opt_clean; write_json {domain_graph}; "
        + "; ".join(commands)
        + "\n"
    )
    domain_log = directory / "domains.log"
    with domain_log.open("w") as output:
        run = subprocess.run(
            ["yosys", "-Q", "-T", "-s", str(domain_script)],
            cwd=ROOT,
            stdout=output,
            stderr=subprocess.STDOUT,
            timeout=1800,
        )
    guard_domains(json.loads(domain_graph.read_text()), TOP)
    domains = domain_log.read_text()
    admitted = (
        run.returncode == 0
        and domains.count("SAT solving finished - model found:") == len(opcodes) ** 2
        and domains.count("SAT solving finished - no model found.") == len(excluded) * 2
    )
    cpu_instruction_proof.supporting_evidence(support)
    stable = before == build.hashes(inputs) and build.generation_current()
    report = {
        "passed": stable and admitted and all(r["accepted"] for r in results),
        "inputs": before,
        "inputs_unchanged": stable,
        "artifacts": build.hashes([*artifacts, graph, domain_graph, domain_script, domain_log]),
        "proofs": results,
        "instruction_count": "unbounded",
        "enable_period": 1,
        "opcodes": [f"{op:02x}" for op in opcodes],
        "nonempty_domains": admitted,
        "admitted_opcode_domains": len(opcodes) ** 2,
        "excluded_opcode_positions": len(excluded) * 2,
        "arbitrary_irq_after_prefix": True,
        "arbitrary_nmi_after_prefix": not irq_only,
        "tools": {"yosys": subprocess.check_output(["yosys", "-V"], text=True).strip()},
        "independent_support": str(support.relative_to(ROOT)),
        "scope": "Unbounded streams in the declared opcode class with freely changing IRQ/NMI after eight real reset edges and the ordinary register/stack/PLP prefix. Exact bus addresses, write direction/bytes, retirement PC and architectural registers/flags. Independent shadow semantics include hardware versus software saved B, IRQ sampling, taken-branch polls, restored-I timing, NMI edge retention and vector hijack. Opcode-only assumption; actual pin histories stay free. All declared opcode pairs are admitted. Actual RTL mutations must fail initialized base cases. The separately executed independent NMOS interrupt collision matrix is bound at periods 1/16/48.",
        "limits": "Declared opcode class and zero simulation power-on profile. Enable period one; synthetic synchronous read responses; no warm restart or subsequent external reset. IRQ/NMI are suppressed during the fixed initialization prefix; NMI stays inactive in the IRQ-only profile. Parent-machine timing, live video and universal Original whole-game equivalence remain separate obligations.",
        "universal_cpu_equivalence": False,
        "universal_game_equivalence": False,
    }
    write_json(directory / "result.json", report)
    if not report["passed"]:
        raise ValueError("Interrupt instruction composition failed; retained full evidence")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--all-documented", action="store_true")
    parser.add_argument("--irq-only", action="store_true")
    args = parser.parse_args()
    qualify(args.all_documented, args.irq_only)
