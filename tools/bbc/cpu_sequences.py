"""Check real CPU instruction sequences against the unchanged NMOS engine."""

import re
import subprocess
import time
from pathlib import Path

from tools.validation.common import ROOT, digest, write_json

from . import build, cycle_reference, prepare


def qualify():
    prepare.main()
    cycle_reference.prepare()
    directory = build.BBC / "cpu-sequences"
    directory.mkdir(exist_ok=True)
    core = build.BBC / "ArletCpuEnabled.v"
    wrapper = ROOT / "rtl/bbc/BbcCpu.v"
    alu = ROOT / "third_party/6502/ALU.v"
    driver = ROOT / "sim/native/bbc_cpu_sequences_test.cpp"
    original = build.BBC / "reference-cycle/m6502.h"
    opcode_inputs = cycle_reference.SOURCE / "official_stimulus.h"
    dependencies = [
        Path(__file__),
        ROOT / "tools/bbc/prepare.py",
        ROOT / "tools/bbc/cpu_bus.py",
        ROOT / "tools/bbc/cycle_reference.py",
        core,
        wrapper,
        alu,
        driver,
        original,
        opcode_inputs,
    ]
    before = build.hashes(dependencies)
    tools = {
        name: subprocess.check_output([name, "--version"], text=True).splitlines()[0]
        for name in ("c++", "verilator")
    }

    def compile_model(model, name):
        native = directory / name
        native.mkdir(exist_ok=True)
        binary = native / "VBbcCpu"
        inputs = build.hashes([model, wrapper, alu, driver, original, opcode_inputs])
        recipe = "-O3 -std=c++17; OPT_FAST=-O3; original m6502.h and production BbcCpu"
        receipt = native / "build.json"
        cache = build.load_receipt("cpu-sequences/" + name + "/build.json")
        if (
            cache.get("inputs") != inputs
            or cache.get("tools") != tools
            or cache.get("recipe") != recipe
            or not binary.exists()
            or cache.get("binary") != digest(binary)
        ):
            for dependency in native.glob("*.d"):
                dependency.unlink()
            with (native / "build.log").open("w") as log:
                subprocess.run(
                    [
                        "verilator",
                        "--cc",
                        "--exe",
                        "--build",
                        "-j",
                        "1",
                        "-Wno-fatal",
                        "--top-module",
                        "BbcCpu",
                        "--Mdir",
                        str(native),
                        "-CFLAGS",
                        "-O3 -std=c++17 -Werror=return-type -iquote"
                        + str(original.parent)
                        + " -iquote"
                        + str(opcode_inputs.parent),
                        "-MAKEFLAGS",
                        "OPT_FAST=-O3",
                        str(wrapper),
                        str(model),
                        str(alu),
                        str(driver),
                    ],
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    check=True,
                    timeout=180,
                )
            if inputs != build.hashes([model, wrapper, alu, driver, original, opcode_inputs]):
                raise ValueError("CPU sequence inputs changed during compilation")
            write_json(
                receipt,
                {
                    "inputs": inputs,
                    "tools": tools,
                    "recipe": recipe,
                    "binary": digest(binary),
                },
            )
        return binary

    binary = compile_model(core, "actual")
    binaries = build.hashes([binary])
    periods = []
    for period in (1, 16, 48):
        started = time.perf_counter()
        result = subprocess.run(
            [str(binary), str(period)], capture_output=True, text=True, timeout=600
        )
        log = directory / f"period{period}.log"
        log.write_text(result.stdout + result.stderr)
        stack = subprocess.run(
            [str(binary), str(period), "stack-fetch"], capture_output=True, text=True, timeout=30
        )
        (directory / f"stack-fetch-{period}.log").write_text(stack.stdout + stack.stderr)
        passed = (
            result.returncode == 0
            and result.stdout == "CPU sequences: 151000 cases; 0 differences\n"
            and stack.returncode == 0
        )
        periods.append(
            {"period": period, "passed": passed, "seconds": time.perf_counter() - started}
        )
        print(f"Sequence period {period}: {passed}; " + result.stdout, end="", flush=True)
    negatives = []
    for name, correct, wrong in (
        ("brk-padding", "adc_sbc <= state != BRK0;", "adc_sbc <= 1;"),
        (
            "decimal-forwarding",
            "adc_bcd <= (plp ? ADD[3] : cld ? 1'b0 : sed ? 1'b1 : D) && state != BRK0;",
            "adc_bcd <= D && state != BRK0;",
        ),
    ):
        text = core.read_text()
        if text.count(correct) != 1:
            raise ValueError("Missing CPU sequence negative-control target: " + name)
        bad = directory / (name + ".v")
        bad.write_text(text.replace(correct, wrong))
        bad_binary = compile_model(bad, name)
        binaries.update(build.hashes([bad_binary]))
        result = subprocess.run([str(bad_binary), "1"], capture_output=True, text=True, timeout=600)
        (directory / (name + ".log")).write_text(result.stdout + result.stderr)
        match = re.fullmatch(
            r"CPU sequences: 151000 cases; ([1-9]\d*) differences\n", result.stdout
        )
        negatives.append(
            {
                "name": name,
                "detected": result.returncode == 1 and match is not None,
                "different_cases": int(match[1]) if match else None,
            }
        )
    old_select = directory / "old-stack-fetch.v"
    text = core.read_text()
    select = "(IRHOLD_valid && state != DECODE) ? IRHOLD : DIMUX;"
    if text.count(select) != 1:
        raise ValueError("Missing stack opcode fetch select")
    old_select.write_text(text.replace(select, "IRHOLD_valid ? IRHOLD : DIMUX;"))
    old_binary = compile_model(old_select, "old-stack-fetch")
    binaries.update(build.hashes([old_binary]))
    result = subprocess.run(
        [str(old_binary), "1", "stack-fetch"], capture_output=True, text=True, timeout=30
    )
    (directory / "old-stack-fetch.log").write_text(result.stdout + result.stderr)
    negatives.append(
        {
            "name": "old-stack-fetch",
            "detected": result.returncode == 1 and "Stack opcode fetch differs" in result.stderr,
        }
    )
    stable = before == build.hashes(dependencies) and binaries == build.hashes(
        ROOT / name for name in binaries
    )
    artifacts = [
        *directory.glob("*.log"),
        *directory.glob("*.v"),
        *(ROOT / name for name in binaries),
    ]
    artifacts.extend(p for p in directory.glob("*/build.*") if p.is_file())
    report = {
        "passed": stable
        and all(p["passed"] for p in periods)
        and all(n["detected"] for n in negatives),
        "inputs": before,
        "inputs_unchanged": stable,
        "tools": tools,
        "periods": periods,
        "negative_controls": negatives,
        "artifacts": build.hashes(artifacts),
        "seed": "0x5eed1234",
        "cases_per_period": 151000,
        "scope": "All 151 documented opcodes with 1,000 deterministic independently varying A/X/Y/S/P, PC and read-response examples each. Both engines execute their own reset and full ordinary stack/register prefix, ending with PLP immediately before the tested opcode. Exact complete prefix/instruction bus, duration and architectural results at enable periods 1/16/48; every physical edge executes. Prefetched CLI/SEI is accounted for by observing I before the final diagnostic-retirement edge. Independent unchanged NMOS engine; no CPU state injection or C translation of the RTL.",
        "limits": "Finite deterministic corpus, IRQ/NMI inactive, prescribed PLP predecessor. Read responses are a shared synthetic external bus, not a complete BBC memory or game fixture. Does not prove arbitrary instruction histories, undocumented opcodes or full machine/game equivalence.",
    }
    write_json(directory / "result.json", report)
    if not report["passed"]:
        raise ValueError("CPU sequence comparison failed; complete diagnostics are retained")
    return report


if __name__ == "__main__":
    qualify()
