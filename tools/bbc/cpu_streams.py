"""Compare long ordinary CPU histories and interrupt service against the pin reference."""

import argparse
import subprocess
import time
from pathlib import Path

from tools.validation.common import ROOT, digest, write_json

from . import build, cycle_reference, prepare


def qualify(stack=False):
    prepare.main()
    cycle_reference.prepare()
    kind = "cpu-stack-streams" if stack else "cpu-streams"
    directory = build.BBC / kind
    directory.mkdir(exist_ok=True)
    driver = ROOT / "sim/native/bbc_cpu_streams_test.cpp"
    wrapper = ROOT / "rtl/bbc/BbcCpu.v"
    core = build.BBC / "ArletCpuEnabled.v"
    alu = ROOT / "third_party/6502/ALU.v"
    original = cycle_reference.DIRECTORY / "m6502.h"
    opcode_inputs = cycle_reference.SOURCE / "official_stimulus.h"
    dependencies = [
        Path(__file__),
        ROOT / "tools/bbc/prepare.py",
        ROOT / "tools/bbc/cpu_bus.py",
        ROOT / "tools/bbc/cycle_reference.py",
        cycle_reference.RECEIPT,
        driver,
        wrapper,
        core,
        alu,
        original,
        opcode_inputs,
    ]
    before = build.hashes(dependencies)
    tools = {
        tool: subprocess.check_output([tool, "--version"], text=True).splitlines()[0]
        for tool in ("c++", "verilator")
    }

    def compile_model(model, cpu_wrapper, name):
        native = directory / name
        native.mkdir(exist_ok=True)
        binary = native / "VBbcCpu"
        inputs = build.hashes([model, cpu_wrapper, driver, alu, original, opcode_inputs])
        recipe = "-O3 C++17; OPT_FAST=-O3; qualified independent NMOS pin reference and production BbcCpu"
        cache = build.load_receipt(kind + "/" + name + "/build.json")
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
                        str(cpu_wrapper),
                        str(model),
                        str(alu),
                        str(driver),
                    ],
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    check=True,
                    timeout=180,
                )
            if inputs != build.hashes([model, cpu_wrapper, driver, alu, original, opcode_inputs]):
                raise ValueError("CPU stream producer changed during compilation")
            write_json(
                native / "build.json",
                {"inputs": inputs, "tools": tools, "recipe": recipe, "binary": digest(binary)},
            )
        return binary

    binary = compile_model(core, wrapper, "actual")
    binaries = build.hashes([binary])
    periods = []
    for period in (1, 16, 48):
        started = time.perf_counter()
        arguments = [str(binary), str(period), *(["stack"] if stack else [])]
        result = subprocess.run(arguments, capture_output=True, text=True, timeout=600)
        log = directory / f"period{period}.log"
        log.write_text(result.stdout + result.stderr)
        expected = "".join(f"stream {case} period {period}: PASS\n" for case in range(64))
        expected += (
            f"CPU streams: 64 cases; 6400000 enabled cycles; period {period}; 0 differences\n"
        )
        passed = result.returncode == 0 and result.stdout == expected and not result.stderr
        periods.append(
            {"period": period, "passed": passed, "seconds": time.perf_counter() - started}
        )
        print(f"CPU stream period {period}: {passed}", flush=True)
    negative_models = []
    target = "adc_bcd <= (plp ? ADD[3] : cld ? 1'b0 : sed ? 1'b1 : D) && state != BRK0;"
    if core.read_text().count(target) != 1:
        raise ValueError("Missing decimal forwarding mutation target")
    bad_core = directory / "wrong-decimal.v"
    bad_core.write_text(core.read_text().replace(target, "adc_bcd <= D && state != BRK0;"))
    negative_models.append(("wrong-decimal", bad_core, wrapper))
    target = ".IRQ(irq)"
    if wrapper.read_text().count(target) != 1:
        raise ValueError("Missing CPU IRQ mutation target")
    bad_wrapper = directory / "wrong-irq.v"
    bad_wrapper.write_text(wrapper.read_text().replace(target, ".IRQ(1'b0)"))
    negative_models.append(("wrong-irq", core, bad_wrapper))
    if stack:
        target = "adc_sbc <= state != BRK0;"
        if core.read_text().count(target) != 1:
            raise ValueError("Missing BRK padding mutation target")
        bad_core = directory / "wrong-brk.v"
        bad_core.write_text(core.read_text().replace(target, "adc_sbc <= 1;"))
        negative_models.append(("wrong-brk", bad_core, wrapper))
    negatives = []
    for name, model, cpu_wrapper in negative_models:
        bad_binary = compile_model(model, cpu_wrapper, name)
        binaries.update(build.hashes([bad_binary]))
        result = subprocess.run(
            [str(bad_binary), "1", *(["stack"] if stack else [])],
            capture_output=True,
            text=True,
            timeout=600,
        )
        (directory / (name + ".log")).write_text(result.stdout + result.stderr)
        negatives.append(
            {
                "name": name,
                "detected": result.returncode == 1 and "FAIL scenario " in result.stderr,
                "first_difference": result.stderr.strip(),
            }
        )
    stable = before == build.hashes(dependencies) and binaries == build.hashes(
        ROOT / name for name in binaries
    )
    artifacts = [
        *directory.glob("*.v"),
        *directory.glob("*.log"),
        *(ROOT / name for name in binaries),
        *directory.glob("*/build.json"),
        *directory.glob("*/build.log"),
    ]
    report = {
        "passed": stable
        and all(p["passed"] for p in periods)
        and all(n["detected"] for n in negatives),
        "inputs": before,
        "inputs_unchanged": stable,
        "periods": periods,
        "negative_controls": negatives,
        "artifacts": build.hashes(artifacts),
        "tools": tools,
        "cases_per_period": 64,
        "enabled_cycles_per_period": 6400000,
        "seed": "0x5eed1234 + scenario * 0x10001",
        "program_family": "balanced-stack-control-flow" if stack else "varied-documented-opcodes",
        "scope": "64 independently executed ordinary programs, each running 100,000 accepted CPU cycles, at enable periods 1/16/48. Every physical edge executes. Exact bus address/direction/write byte, final full memory and architectural registers/flags/PC at each diagnostic retirement after the explicit initialization prefix. Independent RAM arrays and qualified NMOS pin reference; no CPU/register state injection. Includes no interrupt, IRQ, NMI and both signals, repeated entry/RTI, varying flags, stack, arithmetic and addressing interactions. Every generated instruction is documented; absolute writes target scratch RAM, indirect stores are excluded and generated relative offsets are zero.",
        "limits": "Finite deterministic synthetic input programs and prescribed interrupt waves, not universal instruction histories or released Elite-A gameplay. 143 opcodes are eligible for generated bodies, with an additional loop JMP and handler RTI; BRK, JSR/RTS, indirect JMP and indirect stores have separate bus regressions. Reset-only unspecified flag values before the explicit normal prefix are outside architectural comparisons.",
    }
    if stack:
        report["scope"] = (
            "64 independently executed ordinary balanced stack/control-flow programs, each running 100,000 accepted cycles at enable periods 1/16/48, with every physical edge executed. All bus address/direction/write bytes, full final RAM and architectural registers/flags/PC at each retirement after the normal initialization prefix. Eight nested JSR/RTS levels, balanced register/status pushes/pulls, BRK with varying padding, IRQ/NMI and RTI during subroutines, adjacent decimal transitions, indirect stores to scratch RAM, conditional nonzero branches and indirect JMP through a page-end pointer. Independent RAM and qualified NMOS pin engine; no state injection."
        )
        report["limits"] = (
            "Finite deterministic generated instruction programs and prescribed interrupt waves, not universal CPU or original game histories. Stack depth, program contents and indirect pointers are deliberately bounded to keep every executed opcode documented; arbitrary self-modifying or corrupted-return programs are outside this corpus."
        )
    write_json(directory / "result.json", report)
    if not report["passed"]:
        raise ValueError("Long CPU stream comparison failed; all models and logs retained")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stack", action="store_true")
    qualify(stack=parser.parse_args().stack)
