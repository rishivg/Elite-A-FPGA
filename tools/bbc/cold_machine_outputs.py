"""Compare bus, sound, RAM and live RGB from the same independent cold run."""

from __future__ import annotations

import argparse
import gzip
import json
from pathlib import Path

from tools.validation.common import ROOT, digest, write_json

from . import boot_cache, boot_trace, build, cold_machine_video


def original_events(path, kinds, cycles):
    for event in boot_trace.reference_events(path, kinds):
        if event[0] > cycles:
            break
        yield event


def qualify(receipt, mmio_receipt):
    receipt = Path(receipt).resolve()
    video = json.loads(receipt.read_text())
    if video.get("passed") is not True or video.get("universal_game_equivalence") is not False:
        raise ValueError("A complete independently executed live video result is required")
    for field in ("inputs", "artifacts"):
        inventory = video.get(field, {})
        if not inventory or any(digest(ROOT / name) != sha for name, sha in inventory.items()):
            raise ValueError("Live machine video inputs or observations changed")
    compiled = video["native_build"]["inputs"]
    if (
        compiled["fault"] is not None
        or compiled["files"] != build.hashes([ROOT / name for name in compiled["files"]])
        or not build.native_current(3)
    ):
        raise ValueError("Live machine video used different current RTL or instrumentation")
    cycles, prefix = video["requested_cycles"], video["observed"]["prefix"]
    if not cold_machine_video.complete(video["observed"], cycles, prefix):
        raise ValueError("Live machine video comparison is incomplete")
    original_receipt = ROOT / video["original"]["original"]["receipt"]
    pins, _, _, _, producer = cold_machine_video.original_inputs(original_receipt)
    if producer != video["original"]:
        raise ValueError("Independent cold original observations changed")
    reference = pins.parent
    dut = receipt.parent / "dut"
    mmio_receipt = Path(mmio_receipt).resolve()
    mmio = json.loads(mmio_receipt.read_text())
    if (
        mmio.get("passed") is not True
        or mmio.get("master_clock_hz") != 96000000
        or not mmio.get("checks")
        or any(value is not True for value in mmio["checks"].values())
        or mmio["inputs"]["dut"] != build.native_inputs(3)
        or mmio["inputs"]["reference"] != boot_cache.inputs("reference")
        or not mmio.get("original_repeat")
        or any(value is not True for value in mmio["original_repeat"].values())
    ):
        raise ValueError("A current complete independent original MMIO capture is required")
    mmio_guards = {
        **mmio["inputs"]["helpers"],
        **mmio["inputs"]["existing_capture"],
        **mmio["artifacts"],
    }
    if mmio_guards != build.hashes([ROOT / name for name in mmio_guards]):
        raise ValueError("Independent original MMIO inputs or observations changed")
    mmio_reference = ROOT / mmio["reference_directory"]
    metadata = json.loads((mmio_reference / "cold-mmio.json").read_text())
    if metadata["ready"] is not True or metadata["final_cycle"] * 16 < cycles:
        raise ValueError("Independent original MMIO capture is incomplete")
    with (mmio_reference / "cold-mmio.tsv").open() as stream:
        mmio_writes = boot_trace.compare_events(
            (
                (int(clock) * 16, int(address), int(value))
                for clock, address, value in map(str.split, stream)
                if int(clock) * 16 <= cycles
            ),
            boot_trace.dut_events(dut / "bus.tsv"),
        )
    trace = reference / "video-transactions.tsv"
    ram = boot_trace.compare_events(
        original_events(trace, {"ram"}, cycles), boot_trace.dut_events(dut / "ram-writes.tsv")
    )
    video_writes = boot_trace.compare_events(
        original_events(trace, {"crtc", "ula"}, cycles),
        boot_trace.dut_events(dut / "bus.tsv", video=True),
    )
    sound = boot_trace.compare_events(
        (
            (int(clock) * 16, int(value))
            for clock, value in map(str.split, (reference / "psg.tsv").read_text().splitlines())
            if int(clock) * 16 <= cycles
        ),
        (tuple(map(int, line.split())) for line in (dut / "psg.tsv").read_text().splitlines()),
    )
    expected_audio = gzip.decompress((reference / "audio.u8.gz").read_bytes())
    actual_audio = (dut / "audio.u8").read_bytes()
    count = cycles // 64 - 4
    if prefix:
        expected_audio = expected_audio[:count]
    frames = list(map(int, (dut / "frames.tsv").read_text().split()))
    sync = json.loads((reference / "raw-vsync.json").read_text())
    expected_frames = [
        event["cycle"] * 16
        for event in sync["transitions"]
        if event["level"] and event["cycle"] * 16 <= cycles
    ]
    summary = json.loads((dut / "summary.json").read_text())
    checks = {
        "live_rgb_pixel_sync": video["passed"],
        "all_ram_writes": ram["passed"],
        "all_video_bus_writes": video_writes["passed"],
        "all_mmio_writes": mmio_writes["passed"],
        "all_sound_events": sound["passed"],
        "all_audio_samples": actual_audio == expected_audio
        and len(actual_audio) == len(expected_audio) == count,
        "all_raw_vsync_rises": frames == expected_frames
        and len(frames) == summary["frames"]
        and sound["dut_events"] == summary["psg_writes"],
        "own_final_ram": prefix
        or (dut / "boot.ram").read_bytes() == (reference / "boot.ram").read_bytes(),
        "unchanged_inputs": video["inputs"]
        == build.hashes([ROOT / name for name in video["inputs"]])
        and video["artifacts"] == build.hashes([ROOT / name for name in video["artifacts"]])
        and producer == cold_machine_video.original_inputs(original_receipt)[-1]
        and mmio_guards == build.hashes([ROOT / name for name in mmio_guards]),
    }
    result = {
        "passed": all(checks.values()),
        "checks": checks,
        "live_video_receipt": str(receipt.relative_to(ROOT)),
        "live_video_receipt_sha256": digest(receipt),
        "producer_sha256": digest(Path(__file__)),
        "original_mmio_receipt": str(mmio_receipt.relative_to(ROOT)),
        "original_mmio_receipt_sha256": digest(mmio_receipt),
        "ram_writes": ram,
        "video_bus_writes": video_writes,
        "mmio_writes": mmio_writes,
        "sound_events": sound,
        "audio_samples": len(actual_audio),
        "vsync_rises": len(frames),
        "prefix": prefix,
        "duration_cycles": cycles,
        "universal_game_equivalence": False,
        "scope": "All these digital observations come from the same ordinary cold CPU/RAM/machine execution, with passive RGB comparison. Finite declared release, media, clocks and inputs; not arbitrary instruction, device or game histories.",
    }
    write_json(receipt.parent / "whole-machine-outputs.json", result)
    if not result["passed"]:
        raise ValueError("Live whole-machine digital outputs differ: " + str(receipt.parent))
    print("Live whole-machine RGB/bus/audio/RAM: PASS " + str(receipt.parent), flush=True)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("receipt", type=Path)
    parser.add_argument("mmio_receipt", type=Path)
    args = parser.parse_args()
    qualify(args.receipt, args.mmio_receipt)
