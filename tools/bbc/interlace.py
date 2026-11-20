"""Compare complete finite field RGB against the pinned original video renderer."""

from __future__ import annotations

import json
import re
import subprocess
import time
from pathlib import Path

from tools.validation.common import ROOT, source_root, write_json
from tools.validation.setup import verify

from . import build, video96
from .probe import compile_probe, device_rtl

MODULES = ["BbcVideo", "Crtc6845", "BbcVideoAddress"]


def qualify(clock_multiplier=1, teletext=False):
    if clock_multiplier not in {1, 3} or (teletext and clock_multiplier != 3):
        raise ValueError("Teletext requires the 96 MHz common clock")
    verify()
    name = "teletext-reference" if teletext else "interlace"
    script = (
        ROOT
        / "tools/bbc"
        / ("reference_teletext_video.mjs" if teletext else "reference_interlace.mjs")
    )
    source = (
        ROOT
        / "sim/native"
        / (
            "bbc_video96_" + ("teletext" if teletext else name) + "_test.cpp"
            if clock_multiplier == 3
            else "bbc_interlace_test.cpp"
        )
    )
    vendor = source_root("jsbeeb")
    dependencies = [
        Path(__file__),
        script,
        ROOT / "sim/native/bbc_interlace_test.cpp",
        *vendor.glob("src/*.js"),
        ROOT / "tools/validation/reference.lock.json",
    ]
    # The bitmap reference's declared physical-RA memory profile is separately
    # established by the unchanged C mapper; it is not an unchanged jsbeeb RAM path.
    if not teletext:
        from .video_address import qualify as qualify_address

        qualify_address()
        dependencies += [
            build.BBC / "video-address/result.json",
            build.BBC / "video-address/addresses.bin",
            build.BBC / "video-address/oracle",
            build.BBC / "video-address/VBbcVideoAddress",
        ]
    before = build.hashes(dependencies)
    if clock_multiplier == 3:
        binary, inputs = video96.compile(name, source, dependencies)
        directory = video96.DIRECTORY / name
        execution_cwd = video96.DIRECTORY  # Spinal ROM initialization files.
        generated = video96.snapshot()
    else:
        directory = build.BBC / name
        inputs = compile_probe(directory, "BbcVideo", dependencies, MODULES)
        binary = directory / "VBbcVideo"
        execution_cwd = ROOT
        generated = device_rtl(MODULES)
    fixture = directory / "pixels.tsv"
    original = subprocess.run(
        ["node", str(script), str(vendor), str(fixture)],
        check=True,
        capture_output=True,
        text=True,
        timeout=120,
    )
    counts = {"cases": 2, "pixels": 480000} if teletext else {"cases": 16, "pixels": 3047424}
    if json.loads(original.stdout) != counts:
        raise ValueError("Incomplete original field corpus")
    artifacts = build.hashes([binary, fixture])
    started = time.monotonic()
    result = subprocess.run(
        [str(binary), str(fixture)], cwd=execution_cwd, capture_output=True, text=True, timeout=240
    )
    print(result.stdout.strip(), flush=True)
    if result.stderr:
        print(result.stderr.strip(), flush=True)
    if (
        before != build.hashes(dependencies)
        or artifacts != build.hashes([binary, fixture])
        or generated != (video96.snapshot() if clock_multiplier == 3 else device_rtl(MODULES))
        or not build.generation_current()
    ):
        raise ValueError("Field RGB inputs changed during execution")
    match = re.fullmatch(
        r"Field RGB: (\d+) cases; (\d+) pixels; (\d+) differences\n?", result.stdout
    )
    if match is None or {"cases": int(match[1]), "pixels": int(match[2])} != counts:
        raise ValueError("Incomplete field RGB execution")
    passed = result.returncode == 0 and int(match[3]) == 0
    receipt = {
        "passed": passed,
        **counts,
        "differences": int(match[3]),
        "clock_hz": 32000000 * clock_multiplier,
        "inputs": inputs,
        "original_sources": before,
        "artifacts": artifacts,
        "duration_seconds": round(time.monotonic() - started, 3),
        "scope": "Original renderer and CRTC execute actual timed register writes from reset; every active RGB dot in both retained field parities is compared by declared position. No CRTC/teletext state injection. "
        + (
            "Original teletext resampling weights are losslessly decoded into twelve binary physical dots; controls, mosaics, held glyphs, conceal, flash and double height pass through the original cell renderer."
            if teletext
            else "Interlace sync modes 1/3, both character rates and all four bitmap ULA modes. The explicit physical-RA RAM callback corrects jsbeeb's omitted odd-field RA0; the full address mapping is separately compared to unchanged C. The rejected raw reference corpus remains retained."
        ),
        "limits": "Finite active-picture corpus, not arbitrary register transitions, border/cursor timing, mode changes or physical PLL/board/capture acceptance. "
        + (
            "Original jsbeeb intentionally omits character clocks during blanking and documents its early row reset; disagreement in that domain cannot establish physical correctness."
            if teletext
            else "The declared physical-RA RAM profile changes the memory callback; the original counters and renderer remain unchanged."
        ),
    }
    write_json(directory / "result.json", receipt)
    result.check_returncode()
    if not passed:
        raise ValueError(
            "Field RGB differs; complete original fixture and failure receipt retained"
        )
    return receipt


if __name__ == "__main__":
    qualify()
