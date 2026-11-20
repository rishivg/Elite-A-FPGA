"""Audit sealed boot captures using a fresh, unchanged original-machine execution."""

from __future__ import annotations

import argparse
import gzip
import json
import subprocess
import sys
import tempfile
from itertools import zip_longest
from pathlib import Path

from tools.validation.common import ROOT, digest, write_json

from . import build


def audit(run):
    run = Path(run).resolve()
    workspace = next((p for p in run.parents if (p / "tools/bbc/reference.mjs").is_file()), None)
    if workspace is None or workspace == ROOT:
        raise ValueError("Select a frozen isolated execution's run directory")
    manifest = json.loads((run / "manifest.json").read_text())
    snapshots = json.loads((run / "boot-snapshots.json").read_text())
    config = json.loads((run / "boot-reference.json").read_text())
    multiplier = manifest["master_clock_hz"] // 32000000
    if multiplier not in {1, 3} or config.get("power_profile") != "shared-reset":
        raise ValueError("Unsupported boot clock/reset profile")
    if config.get("load_state") or config.get("scenario"):
        raise ValueError("Boot audit must execute independently from reset")
    assets = {}
    for name, expected in manifest["inputs"].items():
        path = (workspace / name).resolve()
        if not path.is_relative_to(workspace) or not path.is_file() or digest(path) != expected:
            raise ValueError("Frozen execution asset changed: " + name)
        assets[path] = expected
    for name, expected in snapshots["hashes"].items():
        path = Path(name).resolve()
        if not path.is_relative_to(workspace) or not path.is_file() or digest(path) != expected:
            raise ValueError("Sealed own boot changed: " + name)
        assets[path] = expected
    if not all(
        snapshots["independent_boot_receipt"]["backends"][b].get("independent_execution")
        for b in ("dut", "reference")
    ):
        raise ValueError("Both own boots require independent execution receipts")
    probe = (
        "from tools.bbc import boot_cache; "
        f"boot_cache.configure(clock_multiplier={multiplier}); "
        "assert boot_cache.valid(), 'Frozen own-machine boots are stale'"
    )
    subprocess.run([sys.executable, "-c", probe], cwd=workspace, check=True, timeout=30)
    helper = ROOT / "tools/bbc/reference_boot_sync.mjs"
    sources = [
        Path(__file__),
        helper,
        run / "manifest.json",
        run / "boot-snapshots.json",
        run / "boot-reference.json",
        *sorted((run / "boot-dut").glob("*")),
        *assets,
    ]
    before = build.hashes(sources)
    directory = Path(tempfile.mkdtemp(prefix="boot-audit-", dir=build.BBC))
    config["output"] = str(directory / "reference")
    config["save_state"] = str(directory / "reference-own.state")
    configuration = directory / "reference.json"
    write_json(configuration, config)
    config_hash = digest(configuration)
    with (directory / "reference.log").open("w") as log:
        result = subprocess.run(
            [
                "node",
                "--import",
                str(helper),
                str(workspace / "tools/bbc/reference.mjs"),
                str(configuration),
            ],
            cwd=workspace,
            stdout=log,
            stderr=subprocess.STDOUT,
            timeout=600,
        )
    result.check_returncode()
    reference = directory / "reference"
    sync = json.loads((reference / "raw-vsync.json").read_text())
    expected = [e["cycle"] * 16 for e in sync["transitions"] if e["level"]]
    actual = list(map(int, (run / "boot-dut/frames.tsv").read_text().split()))

    def events(path, scale):
        return [
            (int(c) * scale, int(byte)) for c, byte in map(str.split, path.read_text().splitlines())
        ]

    sound_ref = events(reference / "psg.tsv", 16)
    sound_dut = events(run / "boot-dut/psg.tsv", 1)
    summary = json.loads((run / "boot-dut/summary.json").read_text())
    reference_audio = gzip.decompress((reference / "audio.u8.gz").read_bytes())
    dut_audio = (run / "boot-dut/audio.u8").read_bytes()
    complete = (
        summary.get("completed") is True
        and summary.get("restored_own_boot_cycles") == 0
        and summary.get("duration_cycles") == sync["final_cycle"] * 16
        and summary.get("frames") == len(actual)
        and summary.get("psg_writes") == len(sound_dut)
        and sync.get("observed_audio") is True
        and len(reference_audio) == len(dut_audio) == summary["duration_cycles"] // 64 - 4
        and (reference / "boot.ram").stat().st_size == 65536
        and (run / "boot-dut/boot.ram").stat().st_size == 65536
    )
    first_sound = next(
        (i for i, (a, b) in enumerate(zip_longest(sound_ref, sound_dut)) if a != b), None
    )
    unchanged = before == build.hashes(sources) and config_hash == digest(configuration)
    report = {
        "passed": unchanged
        and complete
        and expected == actual
        and sound_ref == sound_dut
        and reference_audio == dut_audio
        and (reference / "boot.ram").read_bytes() == (run / "boot-dut/boot.ram").read_bytes(),
        "inputs_unchanged": unchanged,
        "captures_complete": complete,
        "inputs": before,
        "configuration": config_hash,
        "clock_hz": manifest["master_clock_hz"],
        "node": subprocess.check_output(["node", "--version"], text=True).strip(),
        "raw_vsync": {
            "passed": expected == actual,
            "reference_rising_edges": len(expected),
            "dut_rising_edges": len(actual),
            "reference_end_cycle": sync["final_cycle"] * 16,
        },
        "sound_events": {
            "passed": sound_ref == sound_dut,
            "reference_events": len(sound_ref),
            "dut_events": len(sound_dut),
            "first_difference": first_sound,
            "expected": sound_ref[first_sound]
            if first_sound is not None and first_sound < len(sound_ref)
            else None,
            "actual": sound_dut[first_sound]
            if first_sound is not None and first_sound < len(sound_dut)
            else None,
        },
        "final_ram": {
            "passed": (reference / "boot.ram").read_bytes()
            == (run / "boot-dut/boot.ram").read_bytes()
        },
        "digital_waveform": {
            "passed": reference_audio == dut_audio,
            "reference_samples": len(reference_audio),
            "dut_samples": len(dut_audio),
            "sample_rate_hz": 500000,
            "different_samples": sum(a != b for a, b in zip_longest(reference_audio, dut_audio)),
        },
        "artifacts": build.hashes(
            [*reference.glob("*"), directory / "reference-own.state", directory / "reference.log"]
        ),
        "limits": "Raw VSYNC rising edges, accepted PSG event timestamps, every 500 kHz pre-filter digital sound sample and final RAM. Loading RGB, arbitrary boot keys and physical chip acceptance phase need separate comparisons. This audit does not establish full boot or game equivalence.",
    }
    write_json(directory / "result.json", report)
    print(f"Boot audit: raw VSYNC {'PASS' if expected == actual else 'FAIL'}; {len(actual)} edges")
    print(
        f"Accepted sound events: {'PASS' if sound_ref == sound_dut else 'FAIL'}; {len(sound_dut)}"
    )
    print("Receipt: " + str(directory / "result.json"))
    print(
        f"Digital boot waveform: {'PASS' if reference_audio == dut_audio else 'FAIL'}; {len(dut_audio)} samples"
    )
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    raise SystemExit(0 if audit(parser.parse_args().run)["passed"] else 1)
