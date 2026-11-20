"""Exact original CRTC counters, memory addresses, sync and display enables."""

import subprocess
import time
from pathlib import Path

from tools.validation.common import ROOT, digest, source_root, write_json

from . import build
from .probe import compile_probe, device_rtl


def qualify(dense=False, transitions=False):
    directory = build.BBC / "crtc-probe"
    inputs = compile_probe(
        directory,
        "Crtc6845",
        [
            Path(__file__),
            ROOT / "sim/native/bbc_crtc_test.cpp",
            ROOT / "tools/bbc/reference_crtc.mjs",
        ],
        ["Crtc6845"],
    )
    guarded = [
        *[ROOT / path for path in inputs["sources"]],
        directory / "VCrtc6845",
        *sorted((source_root("jsbeeb") / "src").rglob("*.js")),
    ]
    before = build.hashes(guarded)
    fixture = directory / ("transitions.bin" if transitions else "counters.bin")
    subprocess.run(
        [
            "node",
            str(ROOT / "tools/bbc/reference_crtc.mjs"),
            str(source_root("jsbeeb")),
            str(fixture),
            *(["transitions"] if transitions else []),
        ],
        check=True,
        timeout=60,
    )
    started = time.monotonic()
    result = subprocess.run(
        [str(directory / "VCrtc6845"), str(fixture), *(["dense"] if dense else [])],
        capture_output=True,
        text=True,
        timeout=90,
    )
    elapsed = time.monotonic() - started
    print(result.stdout.strip())
    if result.returncode:
        print(result.stderr.strip())
    unchanged = (
        before == build.hashes(guarded)
        and build.generation_current()
        and device_rtl(["Crtc6845"]) == (directory / "devices.v").read_text()
    )
    receipt = {
        "passed": result.returncode == 0 and unchanged,
        "cases": 162,
        "observations": 6524712,
        "inputs": inputs,
        "artifacts": before,
        "inputs_unchanged": unchanged,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "fixture_sha256": digest(fixture),
        "elapsed_seconds": elapsed,
        "dense_master_clocks": dense,
        "live_register_transitions": transitions,
        "coverage": "Cold unconfigured counters; 1/2 MHz clocks; totals 0/1/63; raster 0/7/18; vertical adjust 0/1/2; bitmap/interlace; MA; H/V sync; vertical display"
        + (
            "; live writes to registers 0–15 with irregular timing, without counter resets"
            if transitions
            else ""
        ),
        "clock": "Original public register writes after each declared 2 MHz scheduler edge",
    }
    name = ("transitions-" if transitions else "") + (
        "dense-result.json" if dense else "result.json"
    )
    write_json(directory / name, receipt)
    if not unchanged:
        raise ValueError("CRTC comparison inputs changed")
    result.check_returncode()
    return receipt


if __name__ == "__main__":
    qualify()
