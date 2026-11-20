"""Full teletext field RGB and exact dot periods against unchanged C video/chip models."""

from __future__ import annotations

import json
import re
import subprocess
import time
from pathlib import Path

from tools.validation.common import ROOT, write_json
from tools.validation.setup import verify

from . import build, crtc_registers, video96

DIRECTORY = video96.DIRECTORY / "teletext"


def prepare_reference(directory, counts, cursor=False, corners=False):
    """Reuse only an unchanged, complete original C execution and its bytes."""
    verify()
    directory.mkdir(parents=True, exist_ok=True)
    source = crtc_registers.independent_sources()
    driver = ROOT / "sim/native/bbc_teletext_video_oracle.c"
    support = ROOT / "sim/native/bbc_crtc_register_support.c"
    sources = [
        Path(__file__),
        ROOT / "tools/bbc/crtc_registers.py",
        driver,
        support,
        *[source / name for name in crtc_registers.PIN],
    ]
    oracle = directory / "oracle"
    fixture = directory / "c-pixels.tsv"
    arguments = ["cursor-corners" if corners else "cursor"] if cursor else []
    inputs = {
        "sources": build.hashes(sources),
        "compiler": subprocess.check_output(["cc", "--version"], text=True).splitlines()[0],
        "arguments": arguments,
        "counts": counts,
        "recipe": "-O2 C; headless original video/teletext/renderer; real reset and timed public writes",
    }
    receipt = directory / "c-reference.json"
    try:
        saved = json.loads(receipt.read_text())
    except (OSError, ValueError):
        saved = {}
    files = [oracle, fixture]
    if (
        saved.get("passed") is True
        and saved.get("inputs") == inputs
        and all(path.is_file() for path in files)
        and saved.get("artifacts") == build.hashes(files)
    ):
        return oracle, fixture
    with (directory / "oracle-build.log").open("w") as log:
        subprocess.run(
            [
                "cc",
                "-O2",
                "-ffunction-sections",
                "-fdata-sections",
                "-DBEEBJIT_HEADLESS",
                "-I" + str(source),
                str(driver),
                str(support),
                *[
                    str(source / name)
                    for name in crtc_registers.SOURCES
                    if name not in {"video.c", "teletext.c", "render.c"}
                ],
                "-Wl,--gc-sections",
                "-lm",
                "-lpthread",
                "-o",
                str(oracle),
            ],
            stdout=log,
            stderr=subprocess.STDOUT,
            check=True,
            timeout=120,
        )
    original = subprocess.run(
        [str(oracle), str(fixture), *arguments],
        check=True,
        capture_output=True,
        text=True,
        timeout=90,
    )
    if json.loads(original.stdout.splitlines()[-1]) != counts:
        raise ValueError("Incomplete unchanged C teletext fields")
    if inputs["sources"] != build.hashes(sources):
        raise ValueError("Original C inputs changed during fixture execution")
    write_json(
        receipt,
        {
            "passed": True,
            "inputs": inputs,
            "artifacts": build.hashes(files),
            "original_commit": crtc_registers.COMMIT,
            "scope": "Original C model executed each fixture cold from reset; no DUT inputs or state.",
        },
    )
    return oracle, fixture


def qualify(cursor=False, cold=False, corners=False):
    if corners and not cursor:
        raise ValueError("Cursor corners require the cursor renderer")
    if cold and not cursor:
        raise ValueError("Cold/warm selection applies to teletext cursor fixtures")
    name = "teletext-cursor-cold" if cold else "teletext-cursor-warm" if cursor else "teletext"
    if corners:
        name += "-corners"
    directory = video96.DIRECTORY / name
    counts = {"cases": 64, "pixels": 614400} if cursor else {"cases": 4, "pixels": 960000}
    if corners:
        counts = {"cases": 512, "pixels": 5775360}
    verify()
    source = crtc_registers.independent_sources()
    driver = ROOT / "sim/native/bbc_teletext_video_oracle.c"
    support = ROOT / "sim/native/bbc_crtc_register_support.c"
    cpp = (
        ROOT
        / "sim/native"
        / ("bbc_video96_teletext_cursor_test.cpp" if cursor else "bbc_video96_teletext_test.cpp")
    )
    dependencies = [
        Path(__file__),
        ROOT / "tools/bbc/crtc_registers.py",
        driver,
        support,
        ROOT / "sim/native/bbc_interlace_test.cpp",
        *([ROOT / "sim/native/bbc_own_prefix.h"] if cursor else []),
        *[source / name for name in crtc_registers.PIN],
    ]
    before = build.hashes(dependencies)
    binary, inputs = video96.compile(directory.name, cpp, dependencies, savable=cursor)
    oracle, fixture = prepare_reference(directory, counts, cursor=cursor, corners=corners)
    artifacts = build.hashes([oracle, binary, fixture])
    generated = video96.snapshot()
    started = time.monotonic()
    result = subprocess.run(
        [str(binary), str(fixture), *(["warm"] if cursor and not cold else [])],
        cwd=video96.DIRECTORY,
        capture_output=True,
        text=True,
        timeout=900 if cursor else 180,
    )
    print(result.stdout.strip(), flush=True)
    if result.stderr:
        print(result.stderr.strip(), flush=True)
    if (
        before != build.hashes(dependencies)
        or artifacts != build.hashes([oracle, binary, fixture])
        or generated != video96.snapshot()
    ):
        raise ValueError("Teletext field inputs changed during execution")
    match = re.fullmatch(
        rf"Field RGB: {counts['cases']} cases; {counts['pixels']} pixels; (\d+) differences\n?",
        result.stdout,
    )
    if match is None:
        raise ValueError("Incomplete teletext field execution")
    passed = result.returncode == 0 and int(match[1]) == 0
    receipt = {
        "passed": passed,
        **counts,
        "cursor": cursor,
        "cursor_corners": corners,
        "own_prefix_reuse": cursor and not cold,
        "differences": int(match[1]),
        "clock_hz": 96000000,
        "dot_hz": 12000000,
        "master_cycles_per_dot": 8,
        "inputs": inputs,
        "original_sources": before,
        "artifacts": artifacts,
        "original_commit": crtc_registers.COMMIT,
        "duration_seconds": round(time.monotonic() - started, 3),
        "scope": (
            "Unchanged video.c, teletext.c and render.c execute public reset/register/timing APIs. A read-only observer calls the original render_render, including its cursor overlay, then reconstructs all twelve physical RGB dots from all sixteen rendered pixels, verifying every resampling weight. Every displayed dot in both physical field parities is compared after twenty own fields; one displayed character row with every raster. "
            + (
                "Address-varying glyphs and control/mosaic/flash/double-height text; full, partial, split and hidden raster ranges; all four blink modes; eight cursor addresses at and beyond display boundaries; all four cursor skews and both display skews. Every dot in declared CRTC character positions 0..46 is compared, including both borders. The original renderer's public border option exposes this whole window. The DUT rejects any displayed pixel outside it. Skew and address are coupled in this finite corpus, rather than a complete product. "
                if corners
                else "Space-filled RAM; all eight ULA segment masks, four cursor skews and display skews 0/1. "
            )
            + "No reference state injection or fitted pixel/clock offset."
            if cursor
            else "Unchanged video.c and teletext.c run public reset/register/timing APIs and address-varying RAM. A read-only observer calls the original teletext_data first, then its original renderer; all sixteen emitted resampled pixels must be exactly reconstructed from twelve binary RGB dots. Every active dot is compared across both physical field parities after twenty own fields; the DUT also verifies every dot interval is eight master cycles. Display skew 0/1 (including the normal MODE 7 pipeline); alpha, all character/control values, mosaics, held graphics, conceal, flash and double-height controls. No reference state injection or fitted pixel/clock offset."
        ),
        "programming_schedule": (
            "The DUT executes and saves its own neutral-cursor prefix at the preceding field's VSYNC. Every case restores only that DUT prefix and performs the full register recipe in blanking; every dot of the next two physical fields is compared with the unchanged original cold execution. All executed master edges remain. Prefix identity binds the entire neutral bus recipe; no reference state is restored."
            if cursor and not cold
            else "Both models execute the declared register recipe from reset before warmup."
        ),
        "limits": "Finite settled physical-field RGB comparison. The C fixture establishes zero registers through real public writes after its nominal power-on reset; its startup field numbering differs from the RTL and JavaScript. Pixel keys use physical field parity and coordinates; absolute startup timing is not established here. Standard 1 MHz teletext only; arbitrary mode/register changes, cursor/border phases beyond the listed cases, fast-clock tricks, physical PLL and board acceptance remain unproved. The C model itself documents approximate short-LOSE and DEW behavior; the independent JavaScript blanking/reset counterexample remains separate evidence.",
    }
    write_json(directory / "c-result.json", receipt)
    result.check_returncode()
    if not passed:
        raise ValueError("Teletext RGB differs; original fixture and failure receipt retained")
    return receipt


if __name__ == "__main__":
    qualify()
