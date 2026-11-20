"""Check the split T2 counter against two unchanged, independent VIA models."""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

from tools.validation.common import ROOT, digest, source_root, write_json

from . import build, crtc_registers
from .probe import compile_probe, device_rtl

MODULES = ["ViaControlPins", "ViaSerial", "Via6522"]
COUNTS = {"cases": 1536, "observations": 690092}
DIRECTORY = build.BBC / "via-t2-shift"


def qualify():
    sources = crtc_registers.independent_sources()
    vendor = source_root("jsbeeb") / "src"
    c_sources = [sources / name for name in ["via.c", "timing.c", "util.c", "log.c"]]
    c_inputs = [
        ROOT / "sim/native/bbc_via_t2_shift_oracle.c",
        ROOT / "sim/native/bbc_via_timer_oracle_support.c",
        *[sources / name for name in crtc_registers.PIN],
    ]
    dependencies = [
        Path(__file__),
        ROOT / "tools/bbc/crtc_registers.py",
        ROOT / "sim/native/bbc_via_t2_shift_test.cpp",
        ROOT / "tools/bbc/reference_via_t2_shift.mjs",
        ROOT / "tools/validation/reference.lock.json",
        vendor / "via.js",
        vendor / "scheduler.js",
        *c_inputs,
    ]
    inputs = compile_probe(DIRECTORY, "Via6522", dependencies, MODULES)
    oracle = DIRECTORY / "oracle"
    command = [
        "cc",
        "-O2",
        "-ffunction-sections",
        "-fdata-sections",
        "-DBEEBJIT_HEADLESS",
        "-I" + str(sources),
        *map(str, c_inputs[:2]),
        *map(str, c_sources),
        "-Wl,--gc-sections",
        "-lm",
        "-lpthread",
        "-o",
        str(oracle),
    ]
    oracle_inputs = {
        "sources": build.hashes(c_inputs),
        "compiler": subprocess.check_output(["cc", "--version"], text=True).splitlines()[0],
        "command": command,
    }
    cached = DIRECTORY / "oracle-build.json"
    try:
        receipt = json.loads(cached.read_text())
    except (OSError, ValueError):
        receipt = {}
    if (
        receipt.get("inputs") != oracle_inputs
        or not oracle.is_file()
        or receipt.get("binary") != digest(oracle)
    ):
        with (DIRECTORY / "oracle-build.log").open("w") as log:
            subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=120)
        if oracle_inputs["sources"] != build.hashes(c_inputs):
            raise ValueError("Independent VIA sources changed during compilation")
        write_json(cached, {"inputs": oracle_inputs, "binary": digest(oracle)})
    original = DIRECTORY / "steady-original.bin"
    independent = DIRECTORY / "steady-independent.bin"
    started = time.monotonic()
    generated = subprocess.check_output(
        [
            "node",
            str(ROOT / "tools/bbc/reference_via_t2_shift.mjs"),
            str(source_root("jsbeeb")),
            str(original),
            "steady",
        ],
        text=True,
        timeout=60,
    )
    if json.loads(generated) != COUNTS or original.stat().st_size != (
        12 + COUNTS["cases"] * 8 + COUNTS["observations"] * 8
    ):
        raise ValueError("Incomplete original T2 corpus")
    before = build.hashes([*dependencies, oracle, DIRECTORY / "VVia6522", original])
    c_result = subprocess.run(
        [str(oracle), str(original), str(independent)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    (DIRECTORY / "independent.log").write_text(c_result.stdout + c_result.stderr)
    print(c_result.stdout.strip())
    c_result.check_returncode()
    if digest(original) != digest(independent):
        raise ValueError("Independent VIA models disagree on the complete corpus")
    native = subprocess.run(
        [str(DIRECTORY / "VVia6522"), str(original)],
        capture_output=True,
        text=True,
        timeout=90,
    )
    (DIRECTORY / "comparison.log").write_text(native.stdout + native.stderr)
    print(native.stdout.strip())
    native.check_returncode()
    expected = "VIA T2 shift counter: 690092 observations in 1536 cases; 0 differences"
    if native.stdout.strip() != expected:
        raise ValueError("Incomplete split-counter comparison")
    if (
        not build.generation_current()
        or device_rtl(MODULES) != (DIRECTORY / "devices.v").read_text()
        or before != build.hashes([*dependencies, oracle, DIRECTORY / "VVia6522", original])
    ):
        raise ValueError("T2 comparison sources changed during execution")
    receipt = {
        "passed": True,
        **COUNTS,
        "differences": 0,
        "elapsed_seconds": time.monotonic() - started,
        "inputs": inputs,
        "oracle_inputs": oracle_inputs,
        "beebjit_commit": crtc_registers.COMMIT,
        "artifacts": build.hashes(
            [original, independent, oracle, DIRECTORY / "VVia6522", DIRECTORY / "devices.v"]
        ),
        "coverage": "Every low reload byte; high 00/01/FF; enabling mode 4 before or after an ordinary load; continuous low-byte relatch, high-byte borrow/IRQ, counter-low acknowledgement and coincident IFR clear. Short reloads exercise high-byte wrap from FF.",
        "scope": "Unchanged pinned jsbeeb public VIA and independently compiled unchanged beebjit C public VIA. Their complete expected corpora are byte-identical. The native executes all 32 master clocks per one-MHz operation.",
        "limits": "All serial modes are separately checked by via_serial; this corpus establishes only the declared T2 counter domain. Mode exit around underflow/relatch and T2CH reload coincident with expiry disagree between references; the rejected extended corpus is retained under rejected-transitions and is not jointly qualified. Pulse-counting combined with shift mode and physical phases remain unproved.",
    }
    write_json(DIRECTORY / "result.json", receipt)
    return receipt


if __name__ == "__main__":
    qualify()
