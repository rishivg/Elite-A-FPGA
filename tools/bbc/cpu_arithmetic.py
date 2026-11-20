"""Compare complete immediate arithmetic/logic domains with the NMOS pin engine."""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from pathlib import Path

from tools.validation.common import ROOT, digest, write_json

from . import build, cpu_idle_proof, cycle_reference, prepare

DIRECTORY = build.BBC / "cpu-arithmetic"
CASES = 524288
PERIODS = (1, 16, 48)


def qualify(logic=False, transitions=False):
    if logic and transitions:
        raise ValueError("Select one arithmetic domain")
    directory = (
        build.BBC / "cpu-decimal-transitions"
        if transitions
        else build.BBC / "cpu-logic"
        if logic
        else DIRECTORY
    )
    cases = 2 * 8 * 256 * 256 if transitions else 4 * 16 * 256 * 256 if logic else CASES
    periods = (1, 16) if logic else PERIODS
    definition = (
        ["-DBBC_DECIMAL_TRANSITIONS=1"]
        if transitions
        else ["-DBBC_LOGIC_DOMAIN=1"]
        if logic
        else []
    )
    idle_proof = cpu_idle_proof.qualify()
    prepare.main()
    cycle_reference.prepare()
    directory.mkdir(parents=True, exist_ok=True)
    reference_source = ROOT / "sim/native/bbc_cpu_arithmetic_reference.cpp"
    dut_source = ROOT / "sim/native/bbc_cpu_arithmetic_test.cpp"
    pinned = cycle_reference.DIRECTORY / "m6502-original.h"
    sources = [
        Path(__file__),
        ROOT / "tools/bbc/prepare.py",
        ROOT / "tools/bbc/cpu_bus.py",
        reference_source,
        dut_source,
        ROOT / "sim/native/bbc_cpu_arithmetic_stimulus.h",
        pinned,
        ROOT / "rtl/bbc/BbcCpu.v",
        build.BBC / "ArletCpuEnabled.v",
        ROOT / "third_party/6502/cpu.v",
        ROOT / "third_party/6502/ALU.v",
        ROOT / "tools/bbc/cpu_idle_proof.py",
        cpu_idle_proof.DIRECTORY / "result.json",
    ]
    inputs = {
        "sources": build.hashes(sources),
        "verilator": subprocess.check_output(["verilator", "--version"], text=True).strip(),
        "compiler": subprocess.check_output(["c++", "--version"], text=True).splitlines()[0],
        "recipe": "Separate original NMOS C and production BbcCpu compilations; -O3 -std=c++17; OPT_FAST=-O3; no LTO",
        "domain": "ADC/SBC after SED/CLD/PLP"
        if transitions
        else "ORA/AND/EOR/CMP"
        if logic
        else "ADC/SBC",
    }
    reference = directory / "reference"
    dut = directory / "VBbcCpu"
    receipt = directory / "build.json"
    try:
        saved = json.loads(receipt.read_text())
    except (OSError, ValueError):
        saved = {}
    if (
        saved.get("inputs") != inputs
        or not all(path.is_file() for path in [reference, dut])
        or saved.get("binaries") != build.hashes([reference, dut])
    ):
        with (directory / "reference-build.log").open("w") as log:
            subprocess.run(
                [
                    "c++",
                    "-O3",
                    "-std=c++17",
                    *definition,
                    "-iquote" + str(cycle_reference.DIRECTORY),
                    str(reference_source),
                    "-o",
                    str(reference),
                ],
                cwd=ROOT,
                stdout=log,
                stderr=subprocess.STDOUT,
                check=True,
                timeout=120,
            )
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
                    "BbcCpu",
                    "-CFLAGS",
                    "-O3 -std=c++17 -Werror=return-type -DBBC_CPU_COMPACT_IDLE=1"
                    + (" " + " ".join(definition) if definition else ""),
                    "-MAKEFLAGS",
                    "OPT_FAST=-O3",
                    "--Mdir",
                    str(directory),
                    str(ROOT / "rtl/bbc/BbcCpu.v"),
                    str(build.BBC / "ArletCpuEnabled.v"),
                    str(ROOT / "third_party/6502/ALU.v"),
                    str(dut_source),
                ],
                cwd=ROOT,
                stdout=log,
                stderr=subprocess.STDOUT,
                check=True,
                timeout=120,
            )
        if inputs["sources"] != build.hashes(sources):
            raise ValueError("Arithmetic sources changed during compilation")
        write_json(receipt, {"inputs": inputs, "binaries": build.hashes([reference, dut])})
    fixture = directory / "arithmetic.bin"
    guarded = [*sources, reference, dut]
    before = build.hashes(guarded)
    subprocess.run([str(reference), str(fixture)], check=True, timeout=60)
    expected_header = (
        b"BBCDEC\x01\x00" if transitions else b"BBCLOG\x01\x00" if logic else b"BBCALU\x01\x00"
    ) + cases.to_bytes(4, "little")
    with fixture.open("rb") as stream:
        if stream.read(12) != expected_header or fixture.stat().st_size != 12 + cases * 2:
            raise ValueError("Incomplete original arithmetic corpus")
    fixture_hash = digest(fixture)
    results = []
    for period in periods:
        started = time.monotonic()
        result = subprocess.run(
            [str(dut), str(fixture), str(period)],
            capture_output=True,
            text=True,
            timeout=max(120, period * 10) * max(1, cases // CASES),
        )
        log = directory / f"period-{period}.log"
        log.write_text(result.stdout + result.stderr)
        print(result.stdout.strip(), flush=True)
        results.append(
            {
                "period": period,
                "elapsed_seconds": time.monotonic() - started,
                "passed": result.returncode == 0
                and result.stdout == f"Arithmetic: {cases} cases; period {period}; 0 differences\n",
                "exit_status": result.returncode,
                "log": digest(log),
            }
        )
    negative = None
    negative_artifacts = []
    if transitions:
        target = "adc_bcd <= (plp ? ADD[3] : cld ? 1'b0 : sed ? 1'b1 : D) && state != BRK0;"
        core = build.BBC / "ArletCpuEnabled.v"
        if core.read_text().count(target) != 1:
            raise ValueError("Missing decimal forwarding negative target")
        bad = directory / "old-decimal-sampling.v"
        bad.write_text(core.read_text().replace(target, "adc_bcd <= D && state != BRK0;"))
        native = directory / "negative"
        native.mkdir(exist_ok=True)
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
                    "-CFLAGS",
                    "-O3 -std=c++17 -Werror=return-type -DBBC_CPU_COMPACT_IDLE=1 -DBBC_DECIMAL_TRANSITIONS=1",
                    "-MAKEFLAGS",
                    "OPT_FAST=-O3",
                    "--Mdir",
                    str(native),
                    str(ROOT / "rtl/bbc/BbcCpu.v"),
                    str(bad),
                    str(ROOT / "third_party/6502/ALU.v"),
                    str(dut_source),
                ],
                stdout=log,
                stderr=subprocess.STDOUT,
                check=True,
                timeout=180,
            )
        bad_binary = native / "VBbcCpu"
        bad_hash = digest(bad_binary)
        result = subprocess.run(
            [str(bad_binary), str(fixture), "1"], capture_output=True, text=True, timeout=600
        )
        log = directory / "negative.log"
        log.write_text(result.stdout + result.stderr)
        match = re.fullmatch(
            r"Arithmetic: 1048576 cases; period 1; ([1-9]\d*) differences\n", result.stdout
        )
        negative = {
            "detected": result.returncode == 1
            and match is not None
            and digest(bad_binary) == bad_hash,
            "different_cases": int(match[1]) if match else None,
        }
        negative_artifacts = [bad, bad_binary, log, native / "build.log"]
    unchanged = before == build.hashes(guarded) and fixture_hash == digest(fixture)
    report = {
        "passed": unchanged
        and all(result["passed"] for result in results)
        and (negative is None or negative["detected"]),
        "cases_per_period": cases,
        "periods": results,
        "inputs": inputs,
        "artifacts": {**before, **build.hashes(negative_artifacts)},
        "negative_control": negative,
        "fixture_sha256": fixture_hash,
        "proven_idle_compaction": idle_proof,
        "inputs_unchanged": unchanged,
        "reference_asset": cycle_reference.locked_assets()["m6502-original.h"],
        "scope": (
            "Every ADC/SBC accumulator/operand byte pair and both carries immediately after SED, CLD, PLP setting D and PLP clearing D. Both engines independently execute real instruction/stack prefixes. Exact A and N/V/D/I/Z/C at enable periods 1/16/48. All reset/enabled edges run; disabled edges use the bound all-register idle proof. The old D sampling must fail."
            if transitions
            else "Every ORA/AND/EOR/CMP accumulator/operand byte pair and every C/D/V/I flag prefix; exact accumulator and N/V/D/I/Z/C. Both engines execute their own resets and real-stack PLP prefixes. Enable periods 1 and 16 retain all reset and enabled edges; disabled edges after reset are compacted using the bound, all-register idle-preservation proof with NMI inactive."
            if logic
            else "Every ADC/SBC accumulator/operand byte pair, both carry inputs and both decimal states; exact accumulator and N/V/D/I/Z/C flags. Each engine executes its own reset and legal instruction prefix. Enable periods 1, 16 and 48 retain all reset and enabled edges; disabled edges after reset use the bound all-register idle-preservation proof with NMI inactive. Earlier full-edge results remain retained separately."
        ),
        "limits": "Immediate arithmetic, logic and comparison with IRQ/NMI inactive. Other opcode bus cycles and interrupt timing have separate regressions; this finite complete arithmetic domain does not establish universal CPU or gameplay equivalence.",
    }
    write_json(directory / "result.json", report)
    if not report["passed"]:
        raise ValueError("Arithmetic comparison failed; full fixture and logs retained")
    return report


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--transitions", action="store_true")
    qualify(transitions=parser.parse_args().transitions)
