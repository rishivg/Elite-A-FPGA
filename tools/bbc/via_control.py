"""Compare integrated 6522 output pins against the original public VIA API."""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

from tools.validation.common import ROOT, digest, source_root, write_json

from . import build
from .probe import compile_probe, device_rtl

MODULES = ["ViaControlPins", "ViaSerial", "Via6522"]


def qualify():
    directory = build.BBC / "via-control"
    vendor = source_root("jsbeeb") / "src"
    dependencies = [
        Path(__file__),
        ROOT / "sim/native/bbc_via_control_test.cpp",
        ROOT / "tools/bbc/reference_via_control.mjs",
        ROOT / "tools/validation/reference.lock.json",
        vendor / "via.js",
        vendor / "scheduler.js",
    ]
    inputs = compile_probe(directory, "Via6522", dependencies, MODULES)
    fixture = directory / "pins.bin"
    original = subprocess.run(
        [
            "node",
            str(ROOT / "tools/bbc/reference_via_control.mjs"),
            str(source_root("jsbeeb")),
            str(fixture),
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    counts = {"cases": 1024, "observations": 153600}
    if json.loads(original.stdout) != counts:
        raise ValueError("Incomplete independent VIA control corpus")
    artifacts = [directory / "devices.v", directory / "VVia6522", fixture]
    before = build.hashes([*dependencies, *artifacts])
    started = time.monotonic()
    result = subprocess.run(
        [str(directory / "VVia6522"), str(fixture)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    elapsed = time.monotonic() - started
    print(result.stdout.strip())
    if result.returncode:
        print(result.stderr.strip())
    result.check_returncode()
    expected = "VIA control pins: 153600 observations in 1024 cases; 0 differences"
    if result.stdout.strip() != expected:
        raise ValueError("Incomplete VIA control comparison")
    if (
        not build.generation_current()
        or device_rtl(MODULES) != (directory / "devices.v").read_text()
        or before != build.hashes([*dependencies, *artifacts])
    ):
        raise ValueError("VIA control inputs changed during comparison")
    receipt = {
        "passed": True,
        **counts,
        "differences": 0,
        "elapsed_seconds": elapsed,
        "inputs": inputs,
        "artifacts": before,
        "fixture_sha256": digest(fixture),
        "coverage": "Every PCR byte, both initial CA1/CB1 levels, manual outputs, handshake/pulse writes, ORA without handshake, coincident pulse expiry/retrigger and CB1 completion/restart, pin changes before accepted writes and inactive timer enables.",
        "scope": "Unmodified pinned jsbeeb UserVia public writes and control-pin callbacks versus the control pins in current generated Via6522. Reference outputs are observed, never injected into RTL.",
        "limits": "Declared one-MHz endpoint timing; no physical pin-phase or warm-reset acceptance proof. Serial shift-register modes remain unimplemented; the separate T2 fixture checks the mode-4 split counter. BbcMachine connects CB2 rising edges to the separately checked light-pen latch.",
    }
    write_json(directory / "result.json", receipt)
    return receipt


if __name__ == "__main__":
    qualify()
