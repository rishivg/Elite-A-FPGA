"""Compare VIA-triggered CRTC light-pen captures with original public devices."""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

from tools.validation.common import ROOT, digest, source_root, write_json

from . import build
from .probe import compile_probe, device_rtl

MODULES = ["ViaControlPins", "ViaSerial", "Via6522", "BbcVideo", "Crtc6845", "BbcVideoAddress"]


def qualify():
    directory = build.BBC / "lightpen"
    vendor = source_root("jsbeeb") / "src"
    dependencies = [
        Path(__file__),
        ROOT / "sim/native/bbc_lightpen_test.cpp",
        ROOT / "sim/native/bbc_lightpen_probe.v",
        ROOT / "tools/bbc/reference_lightpen.mjs",
        ROOT / "tools/validation/reference.lock.json",
        vendor / "via.js",
        vendor / "scheduler.js",
        vendor / "video.js",
        vendor / "teletext.js",
        vendor / "teletext_data.js",
        vendor / "bbc-palette.js",
        vendor / "binary.js",
    ]
    inputs = compile_probe(directory, "BbcLightPenProbe", dependencies, MODULES)
    fixture = directory / "pins.bin"
    original = subprocess.run(
        [
            "node",
            str(ROOT / "tools/bbc/reference_lightpen.mjs"),
            str(source_root("jsbeeb")),
            str(fixture),
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=120,
    )
    counts = {"cases": 1536, "observations": 1179648}
    if json.loads(original.stdout) != counts:
        raise ValueError("Incomplete independent light-pen corpus")
    artifacts = [directory / "devices.v", directory / "VBbcLightPenProbe", fixture]
    before = build.hashes([*dependencies, *artifacts])
    started = time.monotonic()
    result = subprocess.run(
        [str(directory / "VBbcLightPenProbe"), str(fixture)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    elapsed = time.monotonic() - started
    print(result.stdout.strip())
    if result.returncode:
        print(result.stderr.strip())
    result.check_returncode()
    expected = "Light pen: 1179648 observations in 1536 cases; 0 differences"
    if result.stdout.strip() != expected:
        raise ValueError("Incomplete light-pen comparison")
    if (
        not build.generation_current()
        or device_rtl(MODULES) != (directory / "devices.v").read_text()
        or before != build.hashes([*dependencies, *artifacts])
    ):
        raise ValueError("Light-pen inputs changed during comparison")
    receipt = {
        "passed": True,
        **counts,
        "differences": 0,
        "elapsed_seconds": elapsed,
        "inputs": inputs,
        "artifacts": before,
        "fixture_sha256": digest(fixture),
        "coverage": "Every PCR byte at both character clock rates in non-interlaced and interlaced CRTC modes; degenerate and ordinary line totals, vertical adjust and raster heights; programmed start-address wrap; manual, handshake and pulse output captures; CB1 completion/restart; coincident expiry/retrigger. Read-only CPU write semantics are checked separately against the unmodified beebjit C model.",
        "scope": "Public methods of unmodified pinned Video and UserVia connected by the original CB2 callback versus exact generated BBC video/VIA modules with the production connection. Every master clock is executed, with no injected device state. Public bus writes program a nonzero sync width before selecting the 2 MHz character clock.",
        "limits": "Declared BBC bus phases and original-model semantics. The jsbeeb CRTC incorrectly accepts R16/R17 CPU writes, which this timing corpus deliberately excludes and the independent C register probe checks. The original zero-width fast-clock startup sync defect is outside this light-pen corpus; fixed-rate CRTC probes check sync separately. Physical light-pen/CB2 electrical timing and shift-register output remain unqualified.",
    }
    write_json(directory / "result.json", receipt)
    return receipt


if __name__ == "__main__":
    qualify()
