"""Exact bitmap cursor pixels from cold starts or private executed prefixes."""

from __future__ import annotations

import argparse
import json
import subprocess
import time
from pathlib import Path

from tools.validation.common import ROOT, source_root, write_json
from tools.validation.setup import verify

from . import build, video96
from .probe import compile_probe, device_rtl

MODULES = ["BbcVideo", "Crtc6845", "BbcVideoAddress"]
COUNTS = {"cases": 298, "pixels": 37838848}


def qualify(cold=False, clock_multiplier=1):
    if clock_multiplier not in {1, 3} or (cold and clock_multiplier == 3):
        raise ValueError("Unsupported cursor clock/capture variant")
    mode = "cold" if cold else "warm"
    directory = (build.BBC if clock_multiplier == 1 else video96.DIRECTORY) / (
        "cursor-cold" if cold else "cursor-warm"
    )
    vendor = source_root("jsbeeb") / "src"
    source = ROOT / "sim/native" / ("bbc_cursor_test.cpp" if cold else "bbc_cursor_warm_test.cpp")
    if clock_multiplier == 3:
        source = ROOT / "sim/native/bbc_video96_cursor_warm_test.cpp"
    dependencies = [
        *([ROOT / "sim/native/bbc_own_prefix.h"] if not cold else []),
        *([ROOT / "sim/native/bbc_cursor_warm_test.cpp"] if clock_multiplier == 3 else []),
        Path(__file__),
        source,
        ROOT / "tools/bbc/reference_cursor.mjs",
        ROOT / "tools/validation/reference.lock.json",
        *(
            vendor / name
            for name in [
                "video.js",
                "teletext.js",
                "teletext_data.js",
                "bbc-palette.js",
                "binary.js",
            ]
        ),
    ]
    if clock_multiplier == 1:
        inputs = compile_probe(directory, "BbcVideo", dependencies, MODULES, savable=not cold)
        binary = directory / "VBbcVideo"
        generated = directory / "devices.v"
        execution_cwd = ROOT
    else:
        binary, inputs = video96.compile("cursor-warm", source, dependencies, savable=True)
        generated = video96.DIRECTORY / "BbcVideo96.v"
        execution_cwd = video96.DIRECTORY
    source_before = build.hashes(dependencies)
    fixture = directory / "original.bin"
    original = subprocess.run(
        [
            "node",
            str(ROOT / "tools/bbc/reference_cursor.mjs"),
            str(source_root("jsbeeb")),
            str(fixture),
            mode,
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=120,
    )
    if json.loads(original.stdout) != COUNTS or source_before != build.hashes(dependencies):
        raise ValueError("Incomplete or changed original cursor corpus")
    artifacts = [generated, binary, fixture]
    before = build.hashes([*dependencies, *artifacts])
    started = time.monotonic()
    result = subprocess.run(
        [str(binary), str(fixture)],
        cwd=execution_cwd,
        capture_output=True,
        text=True,
        timeout=(1200 if cold else 240) * clock_multiplier,
    )
    elapsed = time.monotonic() - started
    print(result.stdout.strip(), flush=True)
    if result.returncode:
        print(result.stderr.strip(), flush=True)
    result.check_returncode()
    label = "Bitmap cursor" if cold else "Bitmap cursor warm"
    if result.stdout.strip() != f"{label}: 37838848 pixels in 298 cases; 0 differences":
        raise ValueError("Incomplete cursor comparison")
    if (
        not build.generation_current()
        or (clock_multiplier == 1 and device_rtl(MODULES) != generated.read_text())
        or (clock_multiplier == 3 and inputs["generation"] != video96.snapshot())
        or before != build.hashes([*dependencies, *artifacts])
    ):
        raise ValueError("Cursor sources or artifacts changed during comparison")
    # The original imports other helpers transitively. Recheck every pinned
    # source byte, not just the direct video dependencies, after execution.
    verify()
    receipt = {
        "passed": True,
        **COUNTS,
        "differences": 0,
        "mode": mode,
        "master_clock_hz": 32000000 * clock_multiplier,
        "elapsed_seconds": elapsed,
        "inputs": inputs,
        "artifacts": before,
        "coverage": "Both character rates; all eight ULA cursor segment masks; all four cursor skews; normal, split, hidden and singleton raster ranges; all blink modes across fields 6/9/17/33; cursor at display and row boundaries. Every one of 512 by 248 pixels is compared per case, including border pixels.",
        "scope": "Public register writes into the unchanged pinned original bitmap renderer versus exact current generated RTL. Declared synchronous memory pattern; all executed master edges are retained. Cold cases program before executing all fields. Warm cases program during preceding-field vertical blanking; RTL reuses eight private prefixes actually executed by that model, never reference state. Only the subsequent complete viewport is compared.",
        "limits": "Warm and cold have explicitly different programming schedules; matching final pixels do not prove arbitrary cursor writes or prefix/suffix state equivalence. Teletext cursor, interlaced RGB, arbitrary mid-scanline writes and physical chip timing remain unqualified. Full game equivalence is not established by this device corpus.",
    }
    write_json(directory / "result.json", receipt)
    return receipt


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cold", action="store_true", help="Execute every case from reset")
    parser.add_argument("--clock-multiplier", type=int, choices=[1, 3], default=1)
    args = parser.parse_args()
    qualify(cold=args.cold, clock_multiplier=args.clock_multiplier)
