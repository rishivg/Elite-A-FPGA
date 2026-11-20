"""Compare changing ADC inputs and completion/restart collisions at both clocks."""

import os
import re
import subprocess
from pathlib import Path

from tools.validation.common import ROOT, digest, source_root, write_json

from . import adc_results, build, video96
from .probe import compile_probe, device_rtl


def start_sampling_negative(directory, fixture, observations):
    """Reject a private ADC variant that samples at start instead of completion."""
    directory = directory / "start-sampling-negative"
    directory.mkdir(exist_ok=True)
    text = device_rtl(["Adc7002"])
    completion = "          sample <= _zz_sample;\n          sample[3 : 0] <= 4'b0000;\n"
    start = "        control <= io_data[3 : 0];"
    selection = "  assign _zz_sample_1 = control[1 : 0];"
    if any(text.count(section) != 1 for section in (completion, start, selection)):
        raise ValueError("Unrecognized ADC sample latch for negative control")
    text = text.replace(completion, "").replace(
        start, start + "\n        sample <= sampleAtStart;\n        sample[3 : 0] <= 4'b0000;"
    )
    text = text.replace(
        selection,
        selection + "\n  wire [15:0] sampleAtStart = io_data[1:0] == 0 ? values_0 : "
        "io_data[1:0] == 1 ? values_1 : io_data[1:0] == 2 ? values_2 : values_3;",
    )
    rtl = directory / "wrong-start-sampling.v"
    if not rtl.exists() or rtl.read_text() != text:
        rtl.write_text(text)
    driver = ROOT / "sim/native/bbc_adc_sampling_test.cpp"
    binary = directory / "VAdc7002"
    inputs = {
        "sources": build.hashes([rtl, driver]),
        "verilator": subprocess.check_output(["verilator", "--version"], text=True).strip(),
        "compiler": subprocess.check_output(["c++", "--version"], text=True).splitlines()[0],
        "recipe": "Adc7002; -O3 -std=c++17 -flto; OPT_FAST=-O3; private negative only",
    }
    saved = build.load_receipt(str(directory.relative_to(build.BBC) / "build.json"))
    if (
        saved.get("inputs") != inputs
        or not binary.exists()
        or saved.get("binary") != digest(binary)
    ):
        with (directory / "build.log").open("w") as log:
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
                    "Adc7002",
                    "-CFLAGS",
                    "-O3 -std=c++17 -flto -Werror=return-type",
                    "-LDFLAGS",
                    "-flto",
                    "-MAKEFLAGS",
                    "OPT_FAST=-O3",
                    "--Mdir",
                    str(directory),
                    str(rtl),
                    str(driver),
                ],
                cwd=ROOT,
                stdout=log,
                stderr=subprocess.STDOUT,
                check=True,
                timeout=120,
            )
        if inputs["sources"] != build.hashes([rtl, driver]):
            raise ValueError("ADC negative-control sources changed during compilation")
        write_json(directory / "build.json", {"inputs": inputs, "binary": digest(binary)})
    before = build.hashes([rtl, driver, binary, fixture])
    result = subprocess.run(
        [str(binary), str(fixture), "1"], capture_output=True, text=True, timeout=120
    )
    log = directory / "run.log"
    log.write_text(result.stdout + result.stderr)
    detected = (
        result.returncode == 1
        and re.fullmatch(
            rf"ADC sampling: 320 cases; {observations} observations; [1-9]\d* differences\n",
            result.stdout,
        )
        is not None
        and before == build.hashes([rtl, driver, binary, fixture])
    )
    receipt = {
        "negative_control_detected": detected,
        "qualified_machine": False,
        "inputs": before,
        "log_sha256": digest(log),
        "stdout": result.stdout,
        "scope": "Private wrong ADC start-time sampling mutation; production RTL is unchanged",
    }
    write_json(directory / "result.json", receipt)
    print("ADC start-time sampling negative rejected: " + str(detected), flush=True)
    return receipt


def qualify():
    adc_results.prepare()
    video96.generate()
    pattern = r"^module\s+Adc7002\b[\s\S]*?^endmodule\b"
    actual96 = re.search(pattern, (video96.DIRECTORY / "BbcMachine96.v").read_text(), re.M)
    actual32 = re.search(pattern, device_rtl(["Adc7002"]), re.M)
    if actual96 is None or actual32 is None or actual96[0] != actual32[0]:
        raise ValueError("ADC sampling probe differs from actual 96 MHz module")
    directory = build.BBC / "adc-sampling"
    dependencies = [
        Path(__file__),
        ROOT / "sim/native/bbc_adc_sampling_test.cpp",
        ROOT / "tools/bbc/reference_adc_sampling.mjs",
        ROOT / "tools/bbc/reference_adc_results.mjs",
    ]
    inputs = compile_probe(directory, "Adc7002", dependencies, ["Adc7002"])
    binary = directory / "VAdc7002"
    guarded = [
        *dependencies,
        directory / "devices.v",
        binary,
        adc_results.RECEIPT,
        *sorted((source_root("jsbeeb") / "src").rglob("*.js")),
    ]
    before = build.hashes(guarded)
    fixture = directory / "sampling.tsv"
    reference = subprocess.run(
        ["node", str(dependencies[2]), str(source_root("jsbeeb")), str(fixture)],
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    )
    match = re.fullmatch(r"ADC sampling: 320 cases; (\d+) observations\n", reference.stdout)
    if match is None:
        raise ValueError("Incomplete original sampling fixture")
    observations = int(match[1])
    fixture_hash = digest(fixture)
    results = []
    for multiplier in (1, 3):
        result = subprocess.run(
            [str(binary), str(fixture), str(multiplier)],
            capture_output=True,
            text=True,
            timeout=240,
        )
        log = directory / f"clock{multiplier}.log"
        log.write_text(result.stdout + result.stderr)
        passed = result.returncode == 0 and result.stdout == (
            f"ADC sampling: 320 cases; {observations} observations; 0 differences\n"
        )
        print(result.stdout + result.stderr, end="", flush=True)
        results.append({"clock_multiplier": multiplier, "passed": passed, "log": digest(log)})
    negative = start_sampling_negative(directory, fixture, observations)
    unchanged = (
        before == build.hashes(guarded)
        and fixture_hash == digest(fixture)
        and build.generation_current()
        and device_rtl(["Adc7002"]) == (directory / "devices.v").read_text()
        and actual96[0]
        == re.search(pattern, (video96.DIRECTORY / "BbcMachine96.v").read_text(), re.M)[0]
        and adc_results.current()
    )
    report = {
        "passed": unchanged
        and all(result["passed"] for result in results)
        and negative["negative_control_detected"],
        "cases_per_clock": 320,
        "observations_per_clock": observations,
        "clocks": results,
        "inputs": inputs,
        "artifacts": before,
        "fixture_sha256": fixture_hash,
        "inputs_unchanged": unchanged,
        "negative_control": negative,
        "scope": "Actual unchanged ADC RTL at 32/96 MHz spacing: all control bytes, both nominal durations, four channels, changing 16-bit values before/at/after completion, retained prior results and cancellation/restart around completion. Independent original scheduler/ADC with the NEC-qualified digital result profile.",
        "limits": "Fixed nominal timing and completion-time sampling are the declared digital profile. This does not establish the physical analogue aperture or value-dependent duration, or arbitrary input histories.",
    }
    write_json(directory / "result.json", report)
    if not report["passed"]:
        raise ValueError("Changing ADC input comparison failed; complete fixture and logs retained")
    return report


if __name__ == "__main__":
    qualify()
