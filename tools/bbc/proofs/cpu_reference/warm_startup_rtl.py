"""Prove source/RTL startup with arbitrary wait cycles and queued warm resets."""

import json
import shutil
import subprocess
from pathlib import Path

from tools.bbc import cpu_interrupt_proof
from tools.bbc.proofs import memory_composition
from tools.bbc.proofs.cpu_reference import foundation, prepared, reset_rtl
from tools.bbc.proofs.cpu_reference.warm_checks import accepted_result
from tools.bbc.proofs.video_cursor import hashes, replace_once
from tools.validation.common import ROOT, digest, write_json

DIRECTORY = ROOT / "build/bbc/cpu-source-warm-startup-rtl"
EXTRA_PORTS = {
    "Alu": ("[7:0]", "ADD"),
    "Load": ("", "load_reg"),
    "Destination": ("[1:0]", "dst_reg"),
    "Pull": ("", "plp"),
    "InterruptState": (
        "[13:0]",
        "{NMI_edge,NMI_1,bbc_irq_sample,bbc_irq_second,bbc_irq_polled,bbc_nmi_sample,bbc_nmi_second,bbc_nmi_polled,bbc_nmi_vector,bbc_branch_irq,bbc_branch_nmi,bbc_nmi_unaged,bbc_nmi_recent,bbc_nmi_recent2}",
    ),
}


def qualify():
    DIRECTORY.mkdir(exist_ok=True)
    parent_path = reset_rtl.DIRECTORY / "result.json"
    parent = memory_composition.sealed(parent_path)
    if parent.get("os_vector") != 0xD9CD:
        raise ValueError("Warm contract requires the verified Model B OS vector")
    native_path = foundation.DIRECTORY / "result.json"
    native_report = memory_composition.sealed(native_path)
    native = foundation.DIRECTORY / "initial"
    model = reset_rtl.DIRECTORY / "source.v"
    if parent["artifacts"].get(str(model.relative_to(ROOT))) != digest(model):
        raise ValueError("Changed preservation-checked startup decoder")
    core, wrapper = ROOT / "build/bbc/ArletCpuEnabled.v", ROOT / "rtl/bbc/BbcCpu.v"
    contract_path = Path(__file__).with_name("reset_rtl_warm.v")
    files = [
        Path(__file__),
        Path(reset_rtl.__file__),
        Path(foundation.__file__),
        Path(prepared.__file__),
        Path(cpu_interrupt_proof.__file__),
        Path(memory_composition.__file__),
        Path(__file__).with_name("warm_checks.py"),
        Path(__file__).parents[1] / "video_cursor.py",
        contract_path,
        parent_path,
        native_path,
        native,
        model,
        core,
        wrapper,
        Path(shutil.which("yosys")),
    ]
    before = hashes(files)
    initial, pins = reset_rtl.native_initialization(native, native_report)
    contract = (
        contract_path.read_text()
        .replace("INITIAL_STATE", f"{initial:064x}")
        .replace("INITIAL_PINS", f"{pins:016x}")
    )
    observed = cpu_interrupt_proof.observed(
        core.read_text(), wrapper.read_text(), ports={**cpu_interrupt_proof.PORTS, **EXTRA_PORTS}
    )
    variants = [
        ("positive", observed),
        (
            "lost-wait-request",
            replace_once(
                observed,
                "if (warm_reset) warm_pending <= 1;",
                "if (warm_reset && enable) warm_pending <= 1;",
            ),
        ),
        ("ignored-request", replace_once(observed, "(warm_pending || warm_reset)", "1'b0")),
    ]
    checks = []
    for name, cpu in variants:
        case = DIRECTORY / name
        case.mkdir(exist_ok=True)
        rtl, property_file, graph, log, witness = [
            case / n
            for n in ("observed-cpu.v", "contract.v", "graph.json", "proof.log", "witness.json")
        ]
        rtl.write_text(cpu)
        property_file.write_text(contract)
        command = f"read_verilog -formal -sv {model} {rtl} {property_file}; prep -top SourceRtlWarmStartup -flatten; check -assert; async2sync; opt; memory_map; opt; techmap; opt; abc -g AND; opt_clean; write_json {graph}"
        cached = prepared.prepare(case, command, [model, rtl, property_file], [graph])
        if any(
            c["type"] in ("$assume", "$check")
            for c in json.loads(graph.read_text())["modules"]["SourceRtlWarmStartup"][
                "cells"
            ].values()
        ):
            raise ValueError("Warm startup acquired an assumption")
        positive = name == "positive"
        prop = "equivalent" if positive else "faultDefined"
        mode = "-tempinduct -initsteps 14 -maxsteps 22" if positive else "-seq 22"
        witness.unlink(missing_ok=True)
        with log.open("w") as out:
            trial = subprocess.run(
                [
                    "yosys",
                    "-Q",
                    "-T",
                    "-p",
                    f"read_json {graph}; sat -set-init-zero {mode} -prove {prop} 1 -verify -timeout 120 -show {prop} -show joined -dump_json {witness}",
                ],
                stdout=out,
                stderr=subprocess.STDOUT,
                timeout=900,
            )
        accepted = (
            (trial.returncode == 0 and "Induction step proven: SUCCESS!" in log.read_text())
            if positive
            else accepted_result(trial.returncode, log.read_text(), witness, prop, False)
        )
        checks.append({"name": name, "accepted": accepted, "preparation_cache_reused": cached})
        print(name + (": PASS" if accepted else ": FAIL"), flush=True)
        if not accepted:
            break
    memory_composition.sealed(parent_path)
    memory_composition.sealed(native_path)
    stable = before == hashes(files)
    passed = stable and len(checks) == 3 and all(c["accepted"] for c in checks)
    write_json(
        DIRECTORY / "result.json",
        {
            "passed": passed,
            "inputs_unchanged": stable,
            "inputs": before,
            "artifacts": hashes(
                [p for p in DIRECTORY.rglob("*") if p.is_file() and p != DIRECTORY / "result.json"]
            ),
            "checks": checks,
            "scope": "Initialized unbounded direct C/RTL startup comparison with arbitrary physical-clock enables, warm requests and discarded read bytes. Both engines use their own request addresses and the verified OS reset vector/LDA/STA bytes; no source or DUT state injection. Reset-stage, pending-request, stack/ALU and architectural-profile invariants are conclusions with no input assumptions. Every address/RW/SYNC request agrees, and all general registers and meaningful flags agree when the first LDA physically retires. Lost disabled-edge request and ignored request faults require defined-source counterexamples.",
            "limits": "Initial logical zero/reset profile and quiet IRQ/NMI until the first LDA retirement. Indefinite waits/restarts may prevent completion. Execution freezes at conditional convergence; actual parent memory/interrupt discharge, later mixed instruction/reset/interrupt histories and universal game equivalence remain separate.",
            "universal_game_equivalence": False,
        },
    )
    if not passed:
        raise ValueError("Warm startup source/RTL proof incomplete")


if __name__ == "__main__":
    qualify()
