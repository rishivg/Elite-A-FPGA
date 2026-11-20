"""Compare public original FDC restart behavior during active commands."""

import json
import os
import re
import subprocess
from pathlib import Path

from tools.validation.common import ROOT, digest, source_root, write_json

from . import build, probe

CASES = (
    "idle",
    "parameters",
    "partial-specify",
    "seek",
    "search-or-settle",
    "read-request",
    "write-request",
    "one-queued-write",
    "two-queued-writes",
    "late-service-error",
    "data-aliases",
    "host-reset-retention",
    "read-request-aliases",
    "write-request-aliases",
)


def complete(result, rows):
    return (
        type(rows) is int
        and rows >= 20
        and result.returncode == 0
        and not result.stderr
        and result.stdout == f"{rows} original restart observations; 0 differences; 1 restarts\n"
    )


def validate_cases(metadata):
    cases = metadata.get("cases", [])
    if (
        tuple(case.get("name") for case in cases) != CASES
        or metadata.get("own_cold_cases") is not True
        or any(type(case.get("rows")) is not int or case["rows"] < 20 for case in cases)
        or metadata.get("rows") != sum(case["rows"] for case in cases)
        or any(case.get("fixture") != f"original.json.{case['name']}.bus.tsv" for case in cases)
    ):
        raise ValueError("Original restart fixture is incomplete")
    return cases


def compile_fault(directory, text, driver, recipe):
    """Compile a deliberately broken copy; retain its source and binary identity."""
    directory.mkdir(exist_ok=True)
    source = directory / "devices.v"
    source.write_text(text)
    binary = directory / "VIntel8271"
    sources = [source, driver, Path(__file__)]
    sources.extend(ROOT / "sim/native" / name for name in ("bbc_fm_media.h", "bbc_disc_payload.h"))
    inputs = {
        "sources": build.hashes(sources),
        "tools": {
            name: subprocess.check_output([name, "--version"], text=True).splitlines()[0]
            for name in ("verilator", "c++")
        },
        "recipe": "Intel8271; -O3 C++17 LTO; " + recipe,
    }
    cached = build.load_receipt(str((directory / "build.json").relative_to(build.BBC)))
    if (
        cached.get("inputs") != inputs
        or not binary.is_file()
        or cached.get("binary") != digest(binary)
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
                    "Intel8271",
                    "-CFLAGS",
                    "-O3 -std=c++17 -flto -Werror=return-type",
                    "-LDFLAGS",
                    "-flto",
                    "-MAKEFLAGS",
                    "-B OPT_FAST=-O3",
                    "--Mdir",
                    str(directory),
                    str(source),
                    str(driver),
                ],
                cwd=ROOT,
                stdout=log,
                stderr=subprocess.STDOUT,
                check=True,
                timeout=180,
            )
        if inputs["sources"] != build.hashes(sources):
            raise ValueError("FDC fault sources changed during compilation")
        write_json(directory / "build.json", {"inputs": inputs, "binary": digest(binary)})
    return binary


def compile_ignored_restart(directory, actual, driver):
    """Compile the actual generated controller with its request input disconnected."""
    header, separator, body = actual.read_text().partition(");")
    broken, count = re.subn(r"\bio_warmReset\b", "1'b0", body)
    if not separator or "io_warmReset" not in header or count < 4:
        raise ValueError("Missing actual FDC restart negative-control target")
    return compile_fault(
        directory, header + separator + broken, driver, "restart input disconnected only"
    )


def qualify():
    directory = build.BBC / "disc-restart"
    driver = ROOT / "sim/native/bbc_disc_restart_test.cpp"
    reference = ROOT / "tools/bbc/reference_disc_restart.mjs"
    sources = [Path(__file__), driver, reference, ROOT / "tools/bbc/reference_drive.mjs"]
    inputs = probe.compile_probe(directory, "Intel8271", sources, ["Intel8271"])
    before = build.hashes([*sources, build.BBC / "disc.ssd"])
    original = directory / "original.json"
    subprocess.run(
        [
            "node",
            str(reference),
            str(source_root("jsbeeb")),
            str(build.BBC / "disc.ssd"),
            str(original),
        ],
        cwd=ROOT,
        check=True,
        timeout=60,
    )
    metadata = json.loads(original.read_text())
    validate_cases(metadata)
    results = []
    artifacts = [original]
    for case in metadata["cases"]:
        fixture = directory / case["fixture"]
        writes = directory / (case["name"] + ".writes.tsv")
        log = directory / (case["name"] + ".log")
        result = subprocess.run(
            [str(directory / "VIntel8271"), str(fixture), str(build.BBC / "disc.ssd"), str(writes)],
            cwd=build.BBC,
            capture_output=True,
            text=True,
            timeout=180,
        )
        log.write_text(result.stdout + result.stderr)
        passed = complete(result, case["rows"])
        results.append({"name": case["name"], "passed": passed})
        artifacts.extend([fixture, writes, log])
        print(f"FDC restart {case['name']}: {passed}", flush=True)
    fault_directory = directory / "ignored-restart"
    fault = compile_ignored_restart(fault_directory, directory / "devices.v", driver)
    fixture = directory / "original.json.read-request.bus.tsv"
    fault_writes = fault_directory / "writes.tsv"
    result = subprocess.run(
        [str(fault), str(fixture), str(build.BBC / "disc.ssd"), str(fault_writes)],
        cwd=build.BBC,
        capture_output=True,
        text=True,
        timeout=180,
    )
    fault_log = fault_directory / "result.log"
    fault_log.write_text(result.stdout + result.stderr)
    detected = (
        result.returncode == 1
        and re.fullmatch(
            r"\d+ original restart observations; [1-9]\d* differences; 1 restarts\n", result.stdout
        )
        is not None
    )
    artifacts.extend(
        [fault_log, fault_writes, fault_directory / "devices.v", fault_directory / "build.json"]
    )
    stable = (
        before == build.hashes([*sources, build.BBC / "disc.ssd"]) and build.generation_current()
    )
    receipt = {
        "passed": stable and all(case["passed"] for case in results) and detected,
        "inputs": inputs,
        "sources": before,
        "inputs_unchanged": stable,
        "cases": metadata["cases"],
        "results": results,
        "negative_control": {"name": "ignored-restart", "detected": detected},
        "artifacts": build.hashes(artifacts),
        "scope": "Actual current generated FDC, every 32 MHz physical edge, against public original reset and clocked host accesses. Each case has independently executed own cold initialization and media. Idle, parameter and SPECIFY handling, seek/search, read and write requests, queued writes and late-service error; retained data/result/special register readbacks. No payload may resume after abort without a new WRITE command. Other BBC peripherals and subsequent aborted-seek/media recovery are not qualified by this component test.",
        "universal_game_equivalence": False,
    }
    write_json(directory / "result.json", receipt)
    if not receipt["passed"]:
        raise ValueError("FDC restart comparison failed; full histories retained")
    return receipt


if __name__ == "__main__":
    qualify()
