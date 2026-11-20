"""Exact original bitmap/ULA pixels and palette changes across bus boundaries."""

import subprocess
from pathlib import Path

from tools.validation.common import ROOT, digest, source_root, write_json

from . import build, video96
from .probe import compile_probe, device_rtl


def qualify(mode_transitions=False, clock_transitions=False, clock_multiplier=1):
    if clock_multiplier not in {1, 3}:
        raise ValueError("Unsupported video master clock")
    if mode_transitions and clock_transitions:
        raise ValueError("Select one ULA transition variant")
    variant = "mode" if mode_transitions else "clock" if clock_transitions else None
    name = (
        "video-clock-pins"
        if clock_transitions
        else f"video-{variant}-transitions"
        if variant
        else "video-probe"
    )
    dependencies = [
        Path(__file__),
        ROOT / "sim/native/bbc_video_test.cpp",
        ROOT / "tools/bbc/reference_video.mjs",
    ]
    modules = ["BbcVideo", "Crtc6845", "BbcVideoAddress"]
    if clock_multiplier == 3:
        binary, inputs = video96.compile(
            name,
            ROOT / "sim/native/bbc_video96_bitmap_test.cpp",
            dependencies,
            savable=True,
        )
        directory = video96.DIRECTORY / name
        execution_cwd = video96.DIRECTORY
        generated = video96.snapshot()
    else:
        directory = build.BBC / name
        inputs = compile_probe(directory, "BbcVideo", dependencies, modules, savable=True)
        binary = directory / "VBbcVideo"
        execution_cwd = ROOT
        generated = device_rtl(modules)
    guarded = [
        *[ROOT / path for path in inputs["sources"]],
        binary,
        *sorted((source_root("jsbeeb") / "src").rglob("*.js")),
    ]
    before = build.hashes(guarded)
    gold = directory / "original"
    gold.mkdir(exist_ok=True)
    subprocess.run(
        [
            "node",
            str(ROOT / "tools/bbc/reference_video.mjs"),
            str(source_root("jsbeeb")),
            str(gold),
            *([variant] if variant else []),
        ],
        check=True,
        timeout=60,
    )
    expected = {str(p.relative_to(gold)): digest(p) for p in gold.glob("*.rgb")}
    expected_pins = {str(p.relative_to(gold)): digest(p) for p in gold.glob("*.pins")}
    expected_sync = {str(p.relative_to(gold)): digest(p) for p in gold.glob("*.sync")}
    timing = digest(gold / "periods.tsv")
    if len(expected) != 200:
        raise ValueError("Incomplete independent video frames")
    if clock_transitions and (len(expected_pins) != 200 or len(expected_sync) != 200):
        raise ValueError("Incomplete independent digital video streams")
    result = subprocess.run(
        [str(binary), str(gold), *([variant] if variant else [])],
        cwd=execution_cwd,
        capture_output=True,
        text=True,
        timeout=90 * clock_multiplier,
    )
    print(result.stdout.strip())
    if result.returncode:
        print(result.stderr.strip())
    unchanged = (
        before == build.hashes(guarded)
        and build.generation_current()
        and generated == (video96.snapshot() if clock_multiplier == 3 else device_rtl(modules))
        and expected == {str(p.relative_to(gold)): digest(p) for p in gold.glob("*.rgb")}
        and timing == digest(gold / "periods.tsv")
        and expected_pins == {str(p.relative_to(gold)): digest(p) for p in gold.glob("*.pins")}
        and expected_sync == {str(p.relative_to(gold)): digest(p) for p in gold.glob("*.sync")}
    )
    receipt = {
        "passed": result.returncode == 0 and unchanged,
        "cases": 200,
        "master_clock_hz": 32000000 * clock_multiplier,
        "pixels": sum(p.stat().st_size for p in gold.glob("*.pins"))
        if clock_transitions
        else 12697600,
        "inputs": inputs,
        "artifacts": before,
        "inputs_unchanged": unchanged,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "live_mode_transitions": mode_transitions,
        "live_clock_transitions": clock_transitions,
        "reference_frames": expected,
        "reference_periods": timing,
        "reference_pins": expected_pins,
        "reference_sync": expected_sync,
        "sync_observations": sum(p.stat().st_size for p in gold.glob("*.sync")),
        "coverage": "1/2 MHz ULA clocks; all four bitmap modes; four constant byte patterns and address-varying memory; "
        + (
            "clock writes"
            if clock_transitions
            else "mode writes"
            if mode_transitions
            else "palette writes"
        )
        + " before/at/after the shared bus boundary",
        "warm_cache": "Eight own-state baselines per machine; no cross-machine state injection",
        "observation": "Every 16 MHz RGB dot, including blanking, and exact field duration; original public ULA decoder and original video-memory reads; no CRT crop"
        if clock_transitions
        else "Exact cropped bitmap fields and field duration",
        "limits": "Bitmap clock transitions with cursor disabled. CRT positioning is excluded from digital-dot comparison; teletext and arbitrary concurrent CRTC/ULA transitions need separate evidence."
        if clock_transitions
        else "Teletext, interlaced RGB and arbitrary CRTC register transitions remain separate obligations",
    }
    write_json(directory / "result.json", receipt)
    if not unchanged:
        raise ValueError("Video comparison inputs changed")
    result.check_returncode()
    return receipt


if __name__ == "__main__":
    qualify()
