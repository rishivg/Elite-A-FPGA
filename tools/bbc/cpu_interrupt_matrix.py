"""Compare documented-opcode IRQ/NMI collisions from ordinary CPU resets."""

import os
import subprocess
import time
from pathlib import Path

from tools.validation.common import ROOT, digest, write_json

from . import build, cycle_reference, prepare

CASES = 96640
CYCLES = 8697600


def complete(result, period):
    """A passing prefix or early exit cannot stand in for the entire matrix."""
    return (
        result.returncode == 0
        and not result.stderr
        and result.stdout
        == (f"Interrupt matrix: {CASES} cases; {CYCLES} enabled cycles; period {period}; PASS\n")
    )


def qualify():
    prepare.main()
    cycle_reference.prepare()
    directory = build.BBC / "cpu-interrupt-matrix"
    directory.mkdir(exist_ok=True)
    driver = ROOT / "sim/native/bbc_cpu_interrupt_matrix_test.cpp"
    wrapper = ROOT / "rtl/bbc/BbcCpu.v"
    core = build.BBC / "ArletCpuEnabled.v"
    alu = ROOT / "third_party/6502/ALU.v"
    original = cycle_reference.DIRECTORY / "m6502.h"
    stimulus = cycle_reference.SOURCE / "official_stimulus.h"
    dependencies = [
        Path(__file__),
        Path(prepare.__file__),
        ROOT / "tools/bbc/cpu_bus.py",
        Path(cycle_reference.__file__),
        cycle_reference.RECEIPT,
        driver,
        wrapper,
        core,
        alu,
        original,
        stimulus,
    ]
    before = build.hashes(dependencies)
    tools = {
        name: subprocess.check_output([name, "--version"], text=True).splitlines()[0]
        for name in ("verilator", "c++")
    }

    def compile_model(model, name):
        native = directory / name
        native.mkdir(exist_ok=True)
        binary = native / "VBbcCpu"
        inputs = build.hashes([model, wrapper, alu, driver, original, stimulus])
        recipe = "-O3 C++17; OPT_FAST=-O3; all physical edges; own reset/RAM; qualified NMOS pin reference"
        cache = build.load_receipt(f"cpu-interrupt-matrix/{name}/build.json")
        if (
            cache.get("inputs") != inputs
            or cache.get("tools") != tools
            or cache.get("recipe") != recipe
            or not binary.exists()
            or cache.get("binary") != digest(binary)
        ):
            with (native / "build.log").open("w") as log:
                subprocess.run(
                    [
                        "verilator",
                        "--cc",
                        "--exe",
                        "--build",
                        "-j",
                        os.environ.get("VERILATOR_JOBS", "1"),
                        "-Wno-fatal",
                        "--top-module",
                        "BbcCpu",
                        "--Mdir",
                        str(native),
                        "-CFLAGS",
                        "-O3 -std=c++17 -Werror=return-type -iquote"
                        + str(original.parent)
                        + " -iquote"
                        + str(stimulus.parent),
                        "-MAKEFLAGS",
                        "-B OPT_FAST=-O3",
                        str(wrapper),
                        str(model),
                        str(alu),
                        str(driver),
                    ],
                    cwd=ROOT,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    check=True,
                    timeout=180,
                )
            if inputs != build.hashes([model, wrapper, alu, driver, original, stimulus]):
                raise ValueError("CPU collision sources changed during compilation")
            write_json(
                native / "build.json",
                {"inputs": inputs, "tools": tools, "recipe": recipe, "binary": digest(binary)},
            )
        return binary

    actual = compile_model(core, "actual")
    periods = []
    for period in (1, 16, 48):
        started = time.monotonic()
        result = subprocess.run(
            [str(actual), str(period)], capture_output=True, text=True, timeout=300
        )
        (directory / f"period{period}.log").write_text(result.stdout + result.stderr)
        passed = complete(result, period)
        periods.append({"period": period, "passed": passed, "seconds": time.monotonic() - started})
        print(f"CPU interrupt collisions period {period}: {passed}", flush=True)
    negatives = []
    binaries = [actual]
    for name, correct, wrong in (
        (
            "old-nmi-poll",
            "if( state == BRK3 ) begin\n            bbc_nmi_sample <= 0;",
            "if( state == BRK3 && bbc_nmi_vector ) begin\n            bbc_nmi_sample <= 0;",
        ),
        (
            "late-vector-selection",
            "else if( CE && state == BRK1 )\n        bbc_nmi_vector <= (NMI_edge || (NMI && !NMI_1)) && !res;",
            "else if( CE && state == BRK2 )\n        bbc_nmi_vector <= (NMI_edge || (NMI && !NMI_1)) && !res;",
        ),
        (
            "late-nmi-ack",
            "else if( CE && state == BRK1 && !res )\n        NMI_edge <= 0;",
            "else if( CE && bbc_nmi_vector && state == BRK3 )\n        NMI_edge <= 0;",
        ),
    ):
        text = core.read_text()
        if text.count(correct) != 1:
            raise ValueError("Changed CPU interrupt mutation target: " + name)
        model = directory / (name + ".v")
        model.write_text(text.replace(correct, wrong))
        binary = compile_model(model, name)
        binaries.append(binary)
        result = subprocess.run([str(binary), "1"], capture_output=True, text=True, timeout=300)
        (directory / (name + ".log")).write_text(result.stdout + result.stderr)
        detected = result.returncode == 1 and result.stderr.startswith(("BUS op ", "ARCH op "))
        negatives.append({"name": name, "detected": detected})
    stable = before == build.hashes(dependencies)
    receipt = {
        "passed": stable
        and all(p["passed"] for p in periods)
        and all(n["detected"] for n in negatives),
        "inputs": before,
        "inputs_unchanged": stable,
        "cases": CASES,
        "enabled_cycles_per_period": CYCLES,
        "periods": periods,
        "negative_controls": negatives,
        "tools": tools,
        "artifacts": build.hashes(
            [
                *binaries,
                *directory.glob("*.log"),
                *directory.glob("*.v"),
                *directory.glob("*/build.json"),
                *directory.glob("*/build.log"),
            ]
        ),
        "scope": "All 151 documented opcodes, both I masks, two addressing boundaries and values 0/128, sixteen input onsets, five held/pulsed/coincident/repeated IRQ/NMI schedules. Independent pin reference and actual CPU each execute their own reset, prefix, memory/stack writes and following instructions. Every bus request, write byte, retirement architecture and final RAM byte compared; all physical edges at enable periods 1/16/48. Includes BRK/IRQ hijacking cutoff and late/repeated NMI retention; no state injection.",
        "limits": "Finite 96,640-case corpus. C/D/V start set, with N/Z established by the loaded value; documented opcodes only. Input levels change at enable-period boundaries, not every sub-cycle phase. Not arbitrary CPU history, warm-reset or universal game equivalence.",
        "universal_cpu_equivalence": False,
        "universal_game_equivalence": False,
    }
    write_json(directory / "result.json", receipt)
    if not receipt["passed"]:
        raise ValueError("CPU interrupt collision matrix failed; all logs/models retained")
    return receipt


if __name__ == "__main__":
    qualify()
