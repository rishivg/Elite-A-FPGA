"""Compare normal timer/ACR histories at both actual BBC CPU endpoints."""

import json
import os
import re
import subprocess
import time
from pathlib import Path

from tools.validation.common import ROOT, digest, source_root, write_json

from . import build, via_serial
from .probe import compile_probe, device_rtl

CASES = 896
OPERATIONS = 79488
MODULES = ["Via6522", "ViaControlPins", "ViaSerial"]


def validate_fixture(path):
    """Reject truncated histories, missing cases and malformed observations."""
    counts = []
    for line in path.read_text().splitlines():
        fields = line.split()
        if fields and fields[0] == "case":
            if len(fields) != 2 or int(fields[1]) != len(counts):
                raise ValueError("Missing or unordered timer history")
            counts.append(0)
            continue
        if not counts or len(fields) != 8:
            raise ValueError("Malformed timer observation")
        wait, write, address, data, value, flags, irq, pb7 = map(int, fields)
        if (
            not 0 <= wait <= 35
            or write not in {0, 1}
            or address not in {0, 4, 5, 6, 7, 8, 9, 11, 13, 14}
            or any(not 0 <= x <= 255 for x in (data, value, flags))
            or irq not in {0, 1}
            or pb7 not in {0, 1}
            or bool(flags & 128) != bool(irq)
        ):
            raise ValueError("Invalid timer bus observation")
        counts[-1] += 1
    if counts != [519] * 128 + [17] * 768:
        raise ValueError("Incomplete timer histories")
    return {"cases": len(counts), "operations": sum(counts)}


def compile_fault(directory, rtl, driver, tools):
    directory.mkdir(exist_ok=True)
    source = directory / "devices.v"
    # Restore the previous production transition; alter no other state logic.
    broken, count = re.subn(
        r"(?m)^  assign timer1OneShot = [^\n]+;$",
        "  assign timer1OneShot = (! acr[6]);",
        rtl.read_text(),
    )
    if count != 1:
        raise ValueError("Missing unique timer free-run negative-control target")
    source.write_text(broken)
    binary = directory / "VVia6522"
    inputs = {
        "sources": build.hashes([source, driver, Path(__file__)]),
        "tools": tools,
        "recipe": "Via6522; -O3 -std=c++17 -flto; OPT_FAST=-O3",
    }
    try:
        cache = json.loads((directory / "build.json").read_text())
    except (OSError, ValueError):
        cache = {}
    if (
        cache.get("inputs") != inputs
        or not binary.is_file()
        or cache.get("binary") != digest(binary)
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
                    "Via6522",
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
        if inputs["sources"] != build.hashes([source, driver, Path(__file__)]):
            raise ValueError("Timer fault sources changed during compilation")
        write_json(directory / "build.json", {"inputs": inputs, "binary": digest(binary)})
    return binary


def qualify():
    directory = build.BBC / "via-timer-history"
    directory.mkdir(exist_ok=True)
    # Verify these are identical modules in both actual generated parents.
    extra = via_serial.profile_sources(3)
    original = source_root("jsbeeb")
    driver = ROOT / "sim/native/bbc_via_timer_history_test.cpp"
    fixture_producer = ROOT / "tools/bbc/reference_via_timer_history.mjs"
    dependencies = [
        Path(__file__),
        driver,
        fixture_producer,
        ROOT / "tools/bbc/via_serial.py",
        *(original / "src" / name for name in ("via.js", "scheduler.js", "keymap.js")),
    ]
    inputs = compile_probe(directory / "actual", "Via6522", dependencies, MODULES)
    binary = directory / "actual/VVia6522"
    fixture = directory / "reference.tsv"
    subprocess.run(
        ["node", str(fixture_producer), str(original), str(fixture)], check=True, timeout=30
    )
    counts = validate_fixture(fixture)
    tools = {
        name: subprocess.check_output([name, "--version"], text=True).splitlines()[0]
        for name in ("node", "verilator", "c++")
    }
    before = build.hashes([*dependencies, *extra, fixture, binary, directory / "actual/devices.v"])
    periods = []
    for multiplier in (1, 3):
        started = time.monotonic()
        result = subprocess.run(
            [str(binary), str(fixture), str(multiplier)],
            capture_output=True,
            text=True,
            timeout=180,
        )
        (directory / f"clock{32 * multiplier}.log").write_text(result.stdout + result.stderr)
        passed = (
            result.returncode == 0
            and result.stdout
            == f"VIA timer histories: {CASES} cases; {OPERATIONS} operations; 0 differences\n"
        )
        periods.append(
            {
                "clock_hz": 32000000 * multiplier,
                "passed": passed,
                "seconds": time.monotonic() - started,
            }
        )
        print(f"Timer history {32 * multiplier} MHz: {passed}", flush=True)
    fault = compile_fault(
        directory / "previous-free-run", directory / "actual/devices.v", driver, tools
    )
    negative = subprocess.run(
        [str(fault), str(fixture), "1"], capture_output=True, text=True, timeout=180
    )
    (directory / "previous-free-run.log").write_text(negative.stdout + negative.stderr)
    match = re.search(
        rf"VIA timer histories: {CASES} cases; {OPERATIONS} operations; ([1-9]\d*) differences\n\Z",
        negative.stdout,
    )
    detected = negative.returncode == 1 and match is not None
    stable = (
        before == build.hashes(ROOT / name for name in before)
        and build.generation_current()
        and device_rtl(MODULES) == (directory / "actual/devices.v").read_text()
    )
    artifacts = [
        *directory.glob("*.log"),
        fixture,
        fault,
        directory / "previous-free-run/devices.v",
        directory / "previous-free-run/build.json",
        directory / "previous-free-run/build.log",
        directory / "actual/build.json",
        directory / "actual/build.log",
    ]
    receipt = {
        "passed": stable and all(p["passed"] for p in periods) and detected,
        "inputs": inputs,
        "sources": before,
        "inputs_unchanged": stable,
        "tools": tools,
        "periods": periods,
        **counts,
        "negative_control": {
            "name": "previous-free-run",
            "detected": detected,
            "differences": int(match[1]) if match else None,
        },
        "artifacts": build.hashes(artifacts),
        "scope": "128 independent normal-reset timer histories with 512 varying accesses each; 768 directed free-run/one-shot changes before/on/after expiry, both PB7 selections and loads 0..31. Ordinary elapsed clocks and bus operations only, no state injection. Exact read values, IFR, IRQ and PB7 at every accepted operation; all physical edges at 32/96 MHz, actual identical generated VIA components checked in both parents.",
        "limits": "Finite histories; shift modes disabled, PB6/pins held stable, high-phase accepted CPU requests and shared cold reset. Not universal VIA, warm-reset, arbitrary interrupt timestamps between observations, or full game equivalence.",
        "universal_via_equivalence": False,
        "universal_game_equivalence": False,
    }
    write_json(directory / "result.json", receipt)
    if not receipt["passed"]:
        raise ValueError("Timer histories failed; complete fixture and diagnostics retained")
    return receipt


if __name__ == "__main__":
    qualify()
