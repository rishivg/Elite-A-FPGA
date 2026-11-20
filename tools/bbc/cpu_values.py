"""Extend documented-opcode bus comparisons from boundary bytes to all bytes."""

import os
import subprocess
from pathlib import Path

from tools.validation.common import ROOT, digest, source_root, write_json

from . import build, cpu_idle_proof, cycle_reference, prepare

DIRECTORY = build.BBC / "cpu-values"
CASES = 9920 * 64


def qualify():
    idle = cpu_idle_proof.qualify()
    prepare.main()
    cycle_reference.prepare()
    DIRECTORY.mkdir(parents=True, exist_ok=True)
    sources = [
        Path(__file__),
        ROOT / "sim/native/bbc_cpu_values_main.cpp",
        ROOT / "sim/native/bbc_cpu_official_bus_test.cpp",
        cycle_reference.SOURCE / "official_stimulus.h",
        ROOT / "rtl/bbc/BbcCpu.v",
        build.BBC / "ArletCpuEnabled.v",
        ROOT / "third_party/6502/ALU.v",
        ROOT / "tools/bbc/cpu_idle_proof.py",
        cpu_idle_proof.DIRECTORY / "result.json",
    ]
    inputs = {
        "sources": build.hashes(sources),
        "compiler": subprocess.check_output(["c++", "--version"], text=True).splitlines()[0],
        "verilator": subprocess.check_output(["verilator", "--version"], text=True).strip(),
        "recipe": "Production BbcCpu; -O3 -std=c++17 -DBBC_CPU_COMPACT_IDLE=1; OPT_FAST=-O3; synchronous memory; no LTO",
    }
    binary = DIRECTORY / "VBbcCpu"
    saved = build.load_receipt("cpu-values/build.json")
    if (
        saved.get("inputs") != inputs
        or not binary.is_file()
        or saved.get("binary") != digest(binary)
    ):
        with (DIRECTORY / "build.log").open("w") as log:
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
                    "-O3 -std=c++17 -Werror=return-type -DBBC_CPU_COMPACT_IDLE=1",
                    "-MAKEFLAGS",
                    "OPT_FAST=-O3",
                    "--Mdir",
                    str(DIRECTORY),
                    *[str(p) for p in sources if p.suffix in {".v", ".cpp"}],
                ],
                cwd=ROOT,
                stdout=log,
                stderr=subprocess.STDOUT,
                check=True,
                timeout=180,
            )
        if inputs["sources"] != build.hashes(sources):
            raise ValueError("CPU value-sweep sources changed during compilation")
        write_json(DIRECTORY / "build.json", {"inputs": inputs, "binary": digest(binary)})
    reference = cycle_reference.DIRECTORY / "official-bus"
    script = ROOT / "tools/bbc/reference_official.mjs"
    oracle_files = [
        reference,
        cycle_reference.RECEIPT,
        script,
        *sorted((source_root("jsbeeb") / "src").rglob("*.js")),
    ]
    oracle_inputs = build.hashes(oracle_files)
    fixture = DIRECTORY / "bus.tsv"
    stimulus = DIRECTORY / "inputs.tsv"
    oracle_log = DIRECTORY / "original.log"
    paths = [fixture, stimulus, oracle_log]
    oracle = build.load_receipt("cpu-values/reference.json")
    if (
        oracle.get("inputs") != oracle_inputs
        or oracle.get("artifacts") != build.hashes(paths)
        or not all(p.is_file() for p in paths)
    ):
        with fixture.open("w") as output, (DIRECTORY / "reference.log").open("w") as log:
            subprocess.run(
                [str(reference), str(stimulus), "all-values"],
                stdout=output,
                stderr=log,
                check=True,
                timeout=120,
            )
        for path in [fixture, stimulus]:
            with path.open() as stream:
                if sum(1 for _ in stream) != CASES:
                    raise ValueError("Incomplete original all-byte bus matrix")
        with oracle_log.open("w") as log:
            subprocess.run(
                [
                    "node",
                    str(script),
                    str(source_root("jsbeeb")),
                    str(stimulus),
                    str(fixture),
                    "all-values",
                ],
                stdout=log,
                stderr=subprocess.STDOUT,
                check=True,
                timeout=240,
            )
        if oracle_inputs != build.hashes(oracle_files):
            raise ValueError("Independent CPU references changed during fixture creation")
        write_json(
            DIRECTORY / "reference.json",
            {"inputs": oracle_inputs, "artifacts": build.hashes(paths)},
        )
    print(oracle_log.read_text().strip(), flush=True)
    guarded = [*sources, binary, *oracle_files, *paths]
    before = build.hashes(guarded)
    results = []
    for period in [1, 16, 48]:
        result = subprocess.run(
            [str(binary), str(fixture), str(period), "all-values"],
            capture_output=True,
            text=True,
            timeout=360,
        )
        log = DIRECTORY / f"period-{period}.log"
        log.write_text(result.stdout + result.stderr)
        print(result.stdout.strip(), flush=True)
        results.append(
            {
                "period": period,
                "passed": result.returncode == 0,
                "exit_status": result.returncode,
                "log_sha256": digest(log),
            }
        )
    unchanged = before == build.hashes(guarded) and cycle_reference.current()
    receipt = {
        "passed": unchanged and all(r["passed"] for r in results),
        "cases_per_period": CASES,
        "periods": results,
        "inputs": inputs,
        "idle_state_proof": idle,
        "idle_edges_omitted": True,
        "artifacts": before,
        "inputs_unchanged": unchanged,
        "scope": "All 151 documented opcodes, every declared byte value, eight C/D/V flag prefixes, both addressing boundaries and three branch boundaries. Exact enabled bus addresses/read-write/data and final PC/A/X/Y/S/N/V/D/I/Z/C. Each engine independently executes its reset and legal prefix. The pin reference is also checked against the unchanged original JavaScript CPU.",
        "limits": "Declared prefixes couple accumulator and operand values; this is not every possible register/operand combination or an arbitrary instruction sequence. Immediate ADC/SBC has a separate complete input-domain scan. IRQ/NMI have separate timing/bus regressions.",
    }
    write_json(DIRECTORY / "result.json", receipt)
    if not receipt["passed"]:
        raise ValueError("All-byte opcode bus comparison failed; full fixtures and logs retained")
    return receipt


if __name__ == "__main__":
    qualify()
