"""Compare continuous restart requests, including requests between CPU enables."""

import os
import subprocess
import time
from pathlib import Path

from tools.validation.common import ROOT, digest, write_json

from . import build, cycle_reference, prepare

CASES = {"normal": 231936, "interrupt": 212608, "branch": 135168}


def complete(result, mode, period):
    if mode not in CASES or type(period) is not int or period not in (1, 16, 48):
        return False
    cases = CASES[mode]
    return (
        result.returncode == 0
        and not result.stderr
        and result.stdout
        == f"Warm reset {mode}: {cases} cases; {cases * 90} enabled cycles; period {period}; PASS\n"
    )


def qualify():
    prepare.main()
    cycle_reference.prepare()
    directory = build.BBC / "cpu-warm-reset"
    directory.mkdir(exist_ok=True)
    driver = ROOT / "sim/native/bbc_cpu_warm_reset_test.cpp"
    wrapper = ROOT / "rtl/bbc/BbcCpu.v"
    core = build.BBC / "ArletCpuEnabled.v"
    alu = ROOT / "third_party/6502/ALU.v"
    original = cycle_reference.DIRECTORY / "m6502.h"
    stimulus = cycle_reference.SOURCE / "official_stimulus.h"
    sources = [
        Path(__file__),
        Path(prepare.__file__),
        Path(cycle_reference.__file__),
        cycle_reference.RECEIPT,
        ROOT / "tools/bbc/cpu_bus.py",
        driver,
        wrapper,
        core,
        alu,
        original,
        stimulus,
    ]
    before = build.hashes(sources)
    tools = {
        name: subprocess.check_output([name, "--version"], text=True).splitlines()[0]
        for name in ("verilator", "c++")
    }

    def compile_model(wrap, model, name):
        native = directory / name
        native.mkdir(exist_ok=True)
        binary = native / "VBbcCpu"
        files = [wrap, model, alu, driver, original, stimulus]
        inputs = build.hashes(files)
        recipe = "-O3 C++17; OPT_FAST=-O3; every physical edge; one-edge restart pulses; independent own CPUs/RAM"
        cached = build.load_receipt(f"cpu-warm-reset/{name}/build.json")
        if (
            cached.get("inputs") != inputs
            or cached.get("tools") != tools
            or cached.get("recipe") != recipe
            or not binary.exists()
            or cached.get("binary") != digest(binary)
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
                        str(wrap),
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
            if inputs != build.hashes(files):
                raise ValueError("Restart sources changed during compilation")
            write_json(
                native / "build.json",
                {"inputs": inputs, "tools": tools, "recipe": recipe, "binary": digest(binary)},
            )
        return binary

    actual = compile_model(wrapper, core, "actual")
    periods = []
    for period in (1, 16, 48):
        for mode in CASES:
            started = time.monotonic()
            result = subprocess.run(
                [str(actual), str(period), mode], capture_output=True, text=True, timeout=900
            )
            (directory / f"{mode}-period{period}.log").write_text(result.stdout + result.stderr)
            passed = complete(result, mode, period)
            periods.append(
                {
                    "mode": mode,
                    "period": period,
                    "passed": passed,
                    "seconds": time.monotonic() - started,
                }
            )
            print(f"CPU restart {mode} period {period}: {passed}", flush=True)
            if not passed:
                write_json(
                    directory / "failed.json",
                    {"passed": False, "inputs": before, "periods": periods},
                )
                raise ValueError("CPU restart differs; complete failure history retained")
    negatives = []
    binaries = [actual]
    for name, target, correct, wrong, period, mode in (
        (
            "early-core-reset",
            "wrapper",
            ".BBC_WARM(warm_second)",
            ".BBC_WARM(warm_accept)",
            1,
            "normal",
        ),
        (
            "lost-between-enables",
            "wrapper",
            "if (warm_reset) warm_pending <= 1;",
            "if (warm_reset && enable) warm_pending <= 1;",
            16,
            "normal",
        ),
        (
            "lost-recent-nmi",
            "core",
            "NMI_edge <= bbc_nmi_recent || bbc_nmi_unaged || (NMI && !NMI_1) ||\n                    ((state == BRA1 && !(CO ^ backwards)) && bbc_nmi_recent2);",
            "NMI_edge <= 0;",
            1,
            "interrupt",
        ),
        (
            "lost-entry-retention",
            "core",
            "((state == JMP0 && (res || brk)) && NMI_edge) ||",
            "1'b0 ||",
            1,
            "interrupt",
        ),
    ):
        text = (wrapper if target == "wrapper" else core).read_text()
        if text.count(correct) != 1:
            raise ValueError("Changed CPU restart fault anchor: " + name)
        model = directory / (name + ".v")
        model.write_text(text.replace(correct, wrong))
        binary = compile_model(
            model if target == "wrapper" else wrapper, model if target == "core" else core, name
        )
        binaries.append(binary)
        result = subprocess.run(
            [str(binary), str(period), mode], capture_output=True, text=True, timeout=900
        )
        (directory / (name + ".log")).write_text(result.stdout + result.stderr)
        negatives.append(
            {
                "name": name,
                "detected": result.returncode == 1
                and result.stderr.startswith(("BUS FAIL ", "ARCH FAIL ", "SYNC FAIL ")),
            }
        )
    stable = before == build.hashes(sources)
    receipt = {
        "passed": stable
        and all(p["passed"] for p in periods)
        and all(n["detected"] for n in negatives),
        "inputs": before,
        "inputs_unchanged": stable,
        "tools": tools,
        "cases_per_period": CASES,
        "total_cases_per_period": sum(CASES.values()),
        "periods": periods,
        "negative_controls": negatives,
        "artifacts": build.hashes(
            [
                *binaries,
                *directory.glob("*.v"),
                *directory.glob("*.log"),
                *directory.glob("*/build.json"),
                *directory.glob("*/build.log"),
            ]
        ),
        "scope": "Independent normal resets and RAM; all 151 documented opcodes, C/D/V and I masks, three PC/addressing contexts, values 0/1/128/255 and eight reset-request onsets. Separate interrupt corpus includes early, late and repeated NMI and simultaneous IRQ; all branch opcodes additionally cross all contexts and values with those schedules. Restart is one physical edge, including between CPU enables. Every bus request, write, SYNC, architectural retirement and final RAM byte checked at periods 1/16/48.",
        "limits": "Digital RES request accepted at the next SYNC in the declared pin-reference profile. Finite corpus; no physical held-RESET behavior, arbitrary histories or BBC peripheral/game reset qualification.",
        "universal_cpu_equivalence": False,
        "universal_game_equivalence": False,
    }
    write_json(directory / "result.json", receipt)
    if not receipt["passed"]:
        raise ValueError("CPU restart controls failed; artifacts retained")
    return receipt


if __name__ == "__main__":
    qualify()
