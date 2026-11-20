"""Compare continuous C/RTL bus histories through startup and mixed interrupts."""

import json
import shutil
import subprocess
from pathlib import Path

from tools.bbc import prepare
from tools.bbc.proofs import memory_composition
from tools.bbc.proofs.cpu_reference import (
    foundation,
    frontend,
    prepared,
    prune,
    reset_rtl,
    short_stream,
    structural,
)
from tools.bbc.proofs.cpu_reference.warm_checks import accepted_result
from tools.bbc.proofs.video_cursor import hashes, replace_once
from tools.validation.common import ROOT, write_json

DIRECTORY = ROOT / "build/bbc/cpu-source-mixed-rtl"
OPCODES = (0, 0x58, 0x78, 0xEA, 0xA9, 0x8D)


def qualify():
    parent_path = foundation.DIRECTORY / "result.json"
    parent = memory_composition.sealed(parent_path)
    DIRECTORY.mkdir(parents=True, exist_ok=True)
    source = ROOT / "build/bbc/reference-cycle/m6502.h"
    full = foundation.DIRECTORY / "source.v"
    core = ROOT / "build/bbc/ArletCpuEnabled.v"
    upstream, alu = ROOT / "third_party/6502/cpu.v", ROOT / "third_party/6502/ALU.v"
    wrapper = ROOT / "rtl/bbc/BbcCpu.v"
    if core.read_text() != prepare.enabled_cpu(upstream.read_text()) + "\n" + prepare.nmos_alu(
        alu.read_text()
    ):
        raise ValueError("Mixed comparison uses stale generated CPU")
    files = [
        Path(__file__),
        Path(__file__).with_suffix(".v"),
        parent_path,
        source,
        full,
        core,
        upstream,
        alu,
        wrapper,
        *[
            Path(module.__file__)
            for module in (
                foundation,
                frontend,
                prepared,
                prune,
                reset_rtl,
                short_stream,
                structural,
                prepare,
                memory_composition,
            )
        ],
        Path(__file__).with_name("warm_checks.py"),
        Path(__file__).parents[1] / "video_cursor.py",
        ROOT / "tools/validation/common.py",
        *[Path(shutil.which(tool)) for tool in ("gcc", "yosys")],
    ]
    before = hashes(files)
    packed, pins = reset_rtl.native_initialization(foundation.DIRECTORY / "initial", parent)
    property_file = DIRECTORY / "contract.v"
    property_file.write_text(
        Path(__file__)
        .with_suffix(".v")
        .read_text()
        .replace("INITIAL_STATE", f"{packed:064x}")
        .replace("INITIAL_PINS", f"{pins:016x}")
    )
    expanded = subprocess.check_output(
        ["gcc", "-E", "-P", "-"], input=frontend.extract(source), text=True
    )
    model = DIRECTORY / "source.v"
    model.write_text(
        prune.prune(structural.generate(short_stream.adapter_ast(expanded, list(OPCODES))))[0]
    )
    complete = DIRECTORY / "full.v"
    complete.write_text(
        replace_once(full.read_text(), "module M6502ReferenceNext(", "module Full(")
    )
    preservation = DIRECTORY / "preservation.v"
    preservation.write_text(
        replace_once(
            reset_rtl.preservation(),
            "op==0 || op==8'ha9 || op==8'h8d || op==8'hea",
            " || ".join(f"op==8'h{opcode:02x}" for opcode in OPCODES),
        )
    )
    graph = DIRECTORY / "preservation.json"
    prepared.prepare(
        DIRECTORY,
        f"read_verilog -formal -sv {complete} {model} {preservation}; prep -top StartupDecoderPreservation -flatten; check -assert; opt; write_json {graph}",
        [complete, model, preservation],
        [graph],
    )
    with (DIRECTORY / "preservation.log").open("w") as output:
        trial = subprocess.run(
            [
                "yosys",
                "-Q",
                "-T",
                "-p",
                f"read_json {graph}; sat -prove equivalent 1 -verify -timeout 180",
            ],
            stdout=output,
            stderr=subprocess.STDOUT,
            timeout=300,
        )
    if (
        trial.returncode
        or "SAT proof finished - no model found: SUCCESS!"
        not in (DIRECTORY / "preservation.log").read_text()
    ):
        raise ValueError("Mixed decoder changed a complete-source output")
    checks = []
    variants = [
        ("positive", core.read_text()),
        (
            "lost-irq-poll",
            replace_once(
                core.read_text(), "bbc_irq_polled <= bbc_irq_second;", "bbc_irq_polled <= 0;"
            ),
        ),
        (
            "lost-nmi-poll",
            replace_once(
                core.read_text(), "bbc_nmi_polled <= bbc_nmi_second;", "bbc_nmi_polled <= 0;"
            ),
        ),
    ]
    for name, body in variants:
        case = DIRECTORY / name
        case.mkdir(exist_ok=True)
        altered, graph, log, witness = [
            case / filename for filename in ("cpu.v", "graph.json", "proof.log", "witness.json")
        ]
        altered.write_text(body)
        prepared.prepare(
            case,
            f"read_verilog -formal -sv {model} {wrapper} {altered} {alu} {property_file}; prep -top MixedDirect -flatten; check -assert; async2sync; opt; memory_map; opt; techmap; opt; abc -g AND; opt_clean; write_json {graph}",
            [model, wrapper, altered, alu, property_file],
            [graph],
        )
        module = json.loads(graph.read_text())["modules"]["MixedDirect"]
        if any(cell["type"] in ("$assume", "$check") for cell in module["cells"].values()):
            raise ValueError("Mixed comparison acquired an assumption")
        positive = name == "positive"
        prop = "equivalent" if positive else "faultDefined"
        witness.unlink(missing_ok=True)
        with log.open("w") as output:
            trial = subprocess.run(
                [
                    "yosys",
                    "-Q",
                    "-T",
                    "-p",
                    f"read_json {graph}; sat -set-init-zero -seq 36 -prove {prop} 1 -verify -timeout 180 -show {prop} -show phase -show pins -show irqInput -show nmiInput -dump_json {witness}",
                ],
                stdout=output,
                stderr=subprocess.STDOUT,
                timeout=300,
            )
        accepted = accepted_result(trial.returncode, log.read_text(), witness, prop, positive)
        checks.append({"name": name, "accepted": accepted})
        print("Mixed C/RTL " + name + (": PASS" if accepted else ": FAIL"), flush=True)
        if not accepted:
            break
    memory_composition.sealed(parent_path)
    stable = before == hashes(files)
    passed = stable and len(checks) == 3 and all(check["accepted"] for check in checks)
    artifacts = [
        property_file,
        model,
        complete,
        preservation,
        DIRECTORY / "preservation.json",
        DIRECTORY / "preservation.log",
        DIRECTORY / "prepared.json",
        DIRECTORY / "prepare.log",
        *[
            path
            for check in checks
            for path in (DIRECTORY / check["name"]).glob("*")
            if path.is_file()
        ],
    ]
    write_json(
        DIRECTORY / "result.json",
        {
            "passed": passed,
            "inputs_unchanged": stable,
            "inputs": before,
            "artifacts": hashes(artifacts),
            "checks": checks,
            "scope": "Continuous direct C/RTL bus comparison for 36 logical clocks. Real native construction/reset and first LDA/STA startup bytes; IRQ/NMI quiet through first LDA retirement, then arbitrary IRQ/NMI levels, CLI/SEI/NOP fetch choices and response bytes. Both engines issue their own requests. Every address, RW, SYNC and written byte is checked without state injection or SAT assumptions. Complete-source preservation checks all 321 decoder outputs; actual lost IRQ/NMI polling faults must fail with defined source histories.",
            "limits": "Bounded logical-clock history and selected instruction set. Does not cover disabled physical edges, warm requests, all instructions or whole-machine/game equivalence. Startup interrupt quiescence and OS memory bindings remain composition premises.",
            "universal_game_equivalence": False,
        },
    )
    if not passed:
        raise ValueError("Mixed source/RTL comparison incomplete")


if __name__ == "__main__":
    qualify()
