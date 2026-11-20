"""Compare cold-start sound and raw VSYNC through the first 128 microseconds."""

from __future__ import annotations

import argparse
import gzip
import json
import subprocess
import tempfile
from pathlib import Path

from tools.validation.common import ROOT, digest, source_root, write_json
from tools.validation.setup import verify

from . import boot_cache, build, cycle_reference
from .capture import native_environment

CYCLES = 4096


def qualify(clock_multiplier=1):
    if clock_multiplier not in {1, 3}:
        raise ValueError("Unsupported master-clock multiplier")
    verify()
    if not build.native_current(clock_multiplier):
        raise ValueError("Build the current whole-machine simulator first")
    if not cycle_reference.current():
        raise ValueError("Independent cycle reference is stale or unqualified")
    disc = build.BBC / "disc.ssd"
    directory = Path(tempfile.mkdtemp(prefix=f"reset-prefix{clock_multiplier}-", dir=build.BBC))
    keys = directory / "keys.tsv"
    keys.write_text("")
    config = {
        **boot_cache.REFERENCE_OPTIONS,
        "cycle_library": str(cycle_reference.BINARY),
        "jsbeeb": str(source_root("jsbeeb")),
        "disc": str(disc),
        "keys": str(keys),
        "cycles": CYCLES,
        "output": str(directory / "reference"),
    }
    configuration = directory / "reference.json"
    write_json(configuration, config)
    helper = ROOT / "tools/bbc/reference_boot_sync.mjs"
    video_trace = ROOT / "tools/bbc/reference_boot_video_trace.mjs"
    binary = build.native_binary(clock_multiplier)

    def inputs():
        return {
            "dut": build.native_inputs(clock_multiplier),
            "reference": boot_cache.inputs("reference"),
            "runner": build.hashes(
                [
                    Path(__file__),
                    helper,
                    video_trace,
                    binary,
                    configuration,
                    keys,
                    disc,
                    ROOT / "tools/bbc/capture.py",
                ]
            ),
        }

    before = inputs()
    # This fixed 128-us regression has the same scheduling scope as short
    # device probes. Queueing it behind hours of gameplay delays fast tests
    # without materially reducing CPU load; long replay captures use permits.
    with (directory / "reference.log").open("w") as log:
        subprocess.run(
            [
                "node",
                "--import",
                str(helper),
                "--import",
                str(video_trace),
                str(ROOT / "tools/bbc/reference.mjs"),
                str(configuration),
            ],
            cwd=ROOT,
            stdout=log,
            stderr=subprocess.STDOUT,
            check=True,
            timeout=60,
        )
    with (directory / "dut.log").open("w") as log:
        execution = subprocess.run(
            [str(binary), str(CYCLES), "-", "-", str(directory / "dut")],
            cwd=build.native_cwd(clock_multiplier),
            env=native_environment(disc=disc, ram_trace=True),
            stdout=log,
            stderr=subprocess.STDOUT,
            timeout=60,
        )
    # The gameplay runner deliberately rejects a prefix as an incomplete game.
    # Keep that guard intact and require this exact, separately labelled scope.
    dut, reference = directory / "dut", directory / "reference"
    summary = json.loads((dut / "summary.json").read_text())
    sync = json.loads((reference / "raw-vsync.json").read_text())
    expected_summary = {
        "completed": False,
        "duration_cycles": CYCLES,
        "clock_hz": 32000000,
        "restored_own_boot_cycles": 0,
        "frames": 1,
        "checkpoints": 0,
        "inputs": 0,
        "analogue_inputs": 0,
        "psg_writes": 1,
    }

    def events(path, scale):
        return [(int(c) * scale, int(v)) for c, v in map(str.split, path.read_text().splitlines())]

    sound_dut = events(dut / "psg.tsv", 1)
    sound_reference = events(reference / "psg.tsv", 16)
    audio_dut = (dut / "audio.u8").read_bytes()
    audio_reference = gzip.decompress((reference / "audio.u8.gz").read_bytes())
    frames_dut = list(map(int, (dut / "frames.tsv").read_text().split()))
    frames_reference = [t["cycle"] * 16 for t in sync["transitions"] if t["level"]]
    video_trace_summary = json.loads((reference / "video-transactions.json").read_text())
    transactions = [
        line.split() for line in (reference / "video-transactions.tsv").read_text().splitlines()
    ]
    writes_reference = [
        (int(c) * 16, int(address), int(data))
        for c, kind, address, data in transactions
        if kind == "ram"
    ]
    writes_dut = [
        tuple(map(int, line.split())) for line in (dut / "ram-writes.tsv").read_text().splitlines()
    ]
    checks = {
        "prefix_scope": execution.returncode == 1 and summary == expected_summary,
        "reference_scope": sync["final_cycle"] * 16 == CYCLES
        and sync["initial_cycle"] == 16
        and sync["observed_audio"] is True,
        "first_sound_write": sound_dut == sound_reference == [(480, 127)],
        "digital_audio": len(audio_dut) == len(audio_reference) == CYCLES // 64 - 4
        and audio_dut == audio_reference,
        "raw_vsync_rise": frames_dut == frames_reference == [272],
        "reference_write_trace_complete": video_trace_summary["initial_cycle"] == 16
        and video_trace_summary["final_cycle"] * 16 == CYCLES
        and len(transactions) == video_trace_summary["transactions"],
        "accepted_ram_writes": len(writes_reference) > 0 and writes_dut == writes_reference,
        "final_ram": (dut / "boot.ram").read_bytes()[:32768]
        == (reference / "video-final.ram").read_bytes()
        == (reference / "boot.ram").read_bytes()[:32768],
        "unchanged_inputs": before == inputs() and build.native_current(clock_multiplier),
    }
    artifacts = [*dut.glob("*"), *reference.glob("*")]
    result = {
        "passed": all(checks.values()),
        "checks": checks,
        "master_clock_hz": 32000000 * clock_multiplier,
        "fixture_cycles": CYCLES,
        "audio_samples": len(audio_dut),
        "ram_writes": len(writes_dut),
        "inputs": before,
        "artifacts": build.hashes(artifacts),
        "native_binary": digest(binary),
        "scope": "Both whole machines execute independently from reset for 128 microseconds, without keys or restored state. Every accepted RAM write clock/address/value, final RAM, exact PSG writes, every digital audio sample and raw VSYNC rise are compared.",
        "limits": "An initial reset regression, not a completed boot or gameplay equivalence result. Full boot RGB, later behavior and physical hardware remain separate.",
    }
    write_json(directory / "result.json", result)
    print(f"Cold reset prefix {result['master_clock_hz']} Hz: {checks}; {directory}", flush=True)
    if not result["passed"]:
        raise ValueError("Whole-machine cold reset prefix differs")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--clock-multiplier", type=int, choices=[1, 3], default=1)
    qualify(parser.parse_args().clock_multiplier)
