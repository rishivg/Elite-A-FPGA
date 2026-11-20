"""Exhaust BRK padding and flag combinations against the unchanged NMOS engine."""

import re
import subprocess
import time
from pathlib import Path

from tools.validation.common import ROOT, digest, write_json

from . import build, cycle_reference, prepare


def qualify():
    prepare.main()
    cycle_reference.prepare()
    directory = build.BBC / "cpu-brk"
    directory.mkdir(exist_ok=True)
    header = ROOT / "sim/native/bbc_cpu_brk_stimulus.h"
    reference_source = ROOT / "sim/native/bbc_cpu_brk_reference.cpp"
    driver = ROOT / "sim/native/bbc_cpu_brk_test.cpp"
    core = build.BBC / "ArletCpuEnabled.v"
    wrapper = ROOT / "rtl/bbc/BbcCpu.v"
    alu = ROOT / "third_party/6502/ALU.v"
    original = build.BBC / "reference-cycle/m6502.h"
    dependencies = [
        Path(__file__),
        ROOT / "tools/bbc/prepare.py",
        ROOT / "tools/bbc/cpu_bus.py",
        core,
        wrapper,
        alu,
        original,
        reference_source,
        header,
        driver,
    ]
    before = build.hashes(dependencies)
    tool_versions = {
        tool: subprocess.check_output([tool, "--version"], text=True).splitlines()[0]
        for tool in ("c++", "verilator")
    }
    reference = directory / "original"
    recipe = "-O3 -std=c++17; unchanged m6502.h; normal instruction prefix"
    cache = build.load_receipt("cpu-brk/reference-build.json")
    ref_inputs = build.hashes([reference_source, header, original])
    if (
        cache.get("inputs") != ref_inputs
        or cache.get("tools") != tool_versions
        or cache.get("recipe") != recipe
        or not reference.exists()
        or cache.get("binary") != digest(reference)
    ):
        subprocess.run(
            [
                "c++",
                "-O3",
                "-std=c++17",
                "-I" + str(original.parent),
                str(reference_source),
                "-o",
                str(reference),
            ],
            check=True,
            timeout=120,
        )
        write_json(
            directory / "reference-build.json",
            {
                "inputs": ref_inputs,
                "tools": tool_versions,
                "recipe": recipe,
                "binary": digest(reference),
            },
        )
    fixture = directory / "original.tsv"
    with fixture.open("w") as output:
        subprocess.run([str(reference)], stdout=output, check=True, timeout=120)
    if fixture.read_text().splitlines()[-1] != "total 65536":
        raise ValueError("Incomplete original BRK fixture")
    fixture_hash = digest(fixture)

    def compile_model(model, name):
        native = directory / name
        native.mkdir(exist_ok=True)
        binary = native / "VBbcCpu"
        inputs = build.hashes([model, wrapper, alu, driver, header])
        recipe = "-O3 -std=c++17; OPT_FAST=-O3; production BbcCpu"
        cache = build.load_receipt("cpu-brk/" + name + "/build.json")
        if (
            cache.get("inputs") != inputs
            or cache.get("tools") != tool_versions
            or cache.get("recipe") != recipe
            or not binary.exists()
            or cache.get("binary") != digest(binary)
        ):
            # Verilator's previous dependency files can name a moved header.
            # Discard only rebuildable dependencies before the fresh build.
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
                        "-O3 -std=c++17 -Werror=return-type",
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
            if inputs != build.hashes([model, wrapper, alu, driver, header]):
                raise ValueError("BRK native inputs changed during compilation")
            write_json(
                native / "build.json",
                {
                    "inputs": inputs,
                    "tools": tool_versions,
                    "recipe": recipe,
                    "binary": digest(binary),
                },
            )
        return binary

    binary = compile_model(core, "actual")
    binary_hash = digest(binary)
    periods = []
    for period in (1, 16, 48):
        started = time.perf_counter()
        result = subprocess.run(
            [str(binary), str(fixture), str(period)], capture_output=True, text=True, timeout=600
        )
        log = directory / f"period{period}.log"
        log.write_text(result.stdout + result.stderr)
        passed = result.returncode == 0 and result.stdout == "BRK: 65536 cases; 0 differences\n"
        periods.append(
            {
                "period": period,
                "passed": passed,
                "seconds": time.perf_counter() - started,
                "log_sha256": digest(log),
            }
        )
        print(f"BRK period {period}: {passed}; " + result.stdout, end="", flush=True)
    text = core.read_text()
    for correct, wrong in (
        ("adc_sbc <= state != BRK0;", "adc_sbc <= 1;"),
        (
            "adc_bcd <= (plp ? ADD[3] : cld ? 1'b0 : sed ? 1'b1 : D) && state != BRK0;",
            "adc_bcd <= D;",
        ),
    ):
        if text.count(correct) != 1:
            raise ValueError("Missing guarded BRK decoder correction")
        text = text.replace(correct, wrong)
    negative = directory / "old-padding-decode.v"
    negative.write_text(text)
    bad_binary = compile_model(negative, "negative")
    bad_hash = digest(bad_binary)
    result = subprocess.run(
        [str(bad_binary), str(fixture), "1"], capture_output=True, text=True, timeout=600
    )
    (directory / "negative.log").write_text(result.stdout + result.stderr)
    match = re.fullmatch(r"BRK: 65536 cases; ([1-9]\d*) differences\n", result.stdout)
    detected = result.returncode == 1 and match is not None
    stable = (
        before == build.hashes(dependencies)
        and fixture_hash == digest(fixture)
        and binary_hash == digest(binary)
        and bad_hash == digest(bad_binary)
    )
    report = {
        "passed": stable and detected and all(p["passed"] for p in periods),
        "inputs": before,
        "inputs_unchanged": stable,
        "tools": tool_versions,
        "periods": periods,
        "negative_control": {
            "detected": detected,
            "different_cases": int(match[1]) if match else None,
        },
        "artifacts": build.hashes(
            [
                fixture,
                reference,
                binary,
                bad_binary,
                negative,
                *directory.glob("*.log"),
                directory / "reference-build.json",
                directory / "actual/build.json",
                directory / "negative/build.json",
            ]
        ),
        "scope": "All 256 BRK padding bytes and all 64 C/Z/I/D/V/N combinations in four declared PC/stack/vector contexts, with varying A/X/Y inputs. Both engines execute their own ordinary initialization prefix and full seven-cycle BRK. Bus reads/writes, pushed status/PC, vector target and all architectural results match at enabled periods 1/16/48. A private restoration of the old padding decode must fail.",
        "limits": "Finite complete padding/flag domain under the declared contexts, not arbitrary CPU instruction histories or full machine/game equivalence.",
    }
    write_json(directory / "result.json", report)
    if not report["passed"]:
        raise ValueError("BRK padding/flag qualification failed; see retained logs")
    return report


if __name__ == "__main__":
    qualify()
