"""Compare cold-boot RAM and video writes at their accepted bus clocks."""

from __future__ import annotations

import argparse
import gzip
import json
import tempfile
from concurrent.futures import ThreadPoolExecutor
from itertools import zip_longest
from pathlib import Path

from tools.validation.common import ROOT, source_root, write_json
from tools.validation.setup import verify

from . import boot_cache, build, cycle_reference
from .capture import capture_pair, native_environment, run


def compare_events(expected, actual):
    """Retain counts and the first mismatch without holding a boot in memory."""
    reference_count = dut_count = different = 0
    first = None
    for index, (reference, dut) in enumerate(zip_longest(expected, actual)):
        reference_count += reference is not None
        dut_count += dut is not None
        if reference != dut:
            different += 1
            if first is None:
                first = {"index": index, "reference": reference, "dut": dut}
    return {
        "passed": different == 0 and reference_count > 0,
        "reference_events": reference_count,
        "dut_events": dut_count,
        "different_events": different,
        "first_difference": first,
    }


def reference_events(path, kinds):
    with path.open() as stream:
        for line in stream:
            cycle, kind, address, data = line.split()
            if kind in kinds:
                # The shared-reset profile configures the original ULA through
                # its public API before the CPU runs. It is not a guest bus
                # transaction. Reject any other non-bus register event.
                if kind in {"crtc", "ula"} and int(address) < 0xFE00:
                    if (int(cycle), kind, int(address), int(data)) != (16, "ula", 0, 0):
                        raise ValueError("Unexpected non-bus video transaction")
                    continue
                yield (int(cycle) * 16, int(address), int(data))


def dut_events(path, video=False):
    with path.open() as stream:
        for line in stream:
            cycle, address, data, *_ = map(int, line.split())
            if not video or 0xFE00 <= address < 0xFE08 or 0xFE20 <= address < 0xFE30:
                yield (cycle, address, data)


def qualify(clock_multiplier=1, cycles=boot_cache.BOOT_CYCLES, reference_receipt=None):
    if type(cycles) is not int or cycles <= 256 or cycles % 16:
        raise ValueError("Capture duration must be a positive whole CPU-slot boundary after reset")
    verify()
    if not build.native_current(clock_multiplier) or not cycle_reference.current():
        raise ValueError("Build current whole-machine and independent cycle simulators first")
    directory = Path(tempfile.mkdtemp(prefix=f"boot-trace{clock_multiplier}-", dir=build.BBC))
    dut, reference = directory / "dut", directory / "reference"
    keys, disc = build.BBC / "boot.keys", build.BBC / "disc.ssd"
    full_boot = cycles == boot_cache.BOOT_CYCLES
    if not full_boot:
        keys = directory / "prefix.keys"
        keys.write_text(
            "".join(
                line + "\n"
                for line in (build.BBC / "boot.keys").read_text().splitlines()
                if int(line.split()[0]) < cycles
            )
        )
    configuration = directory / "reference.json"
    write_json(
        configuration,
        {
            **boot_cache.REFERENCE_OPTIONS,
            "cycle_library": str(cycle_reference.BINARY),
            "jsbeeb": str(source_root("jsbeeb")),
            "disc": str(disc),
            "keys": str(keys),
            "cycles": cycles,
            "output": str(reference),
            **({"save_state": str(directory / "reference-own.state")} if full_boot else {}),
        },
    )
    sync_helper = ROOT / "tools/bbc/reference_boot_sync.mjs"
    video_helper = ROOT / "tools/bbc/reference_boot_video_trace.mjs"
    producer = None
    observers = [
        ROOT / "tools/bbc" / name
        for name in (
            "reference_boot_video_trace.mjs",
            "reference_boot_video_pins.mjs",
            "reference_boot_sync.mjs",
            "reference_capture.mjs",
        )
    ]
    if reference_receipt is not None:
        from .loading_video import original_capture

        reference_receipt = Path(reference_receipt).resolve()
        reference, producer = original_capture(reference_receipt, cycles, keys, observers)

    def inputs():
        return {
            "dut": build.native_inputs(clock_multiplier),
            "reference": boot_cache.inputs("reference"),
            "runner": build.hashes(
                [
                    Path(__file__),
                    ROOT / "tools/bbc/capture.py",
                    sync_helper,
                    video_helper,
                    ROOT / "tools/bbc/loading_video.py",
                    configuration,
                    keys,
                    disc,
                    build.native_binary(clock_multiplier),
                ]
            ),
            "reused_original": producer,
        }

    before = inputs()
    print(f"Independent cold boot, {clock_multiplier * 32000000} Hz: {directory}", flush=True)
    with ThreadPoolExecutor(max_workers=2) as pool:
        capture_pair(
            pool,
            lambda: run(
                [
                    str(build.native_binary(clock_multiplier)),
                    str(cycles),
                    str(keys),
                    "-",
                    str(dut),
                ],
                build.native_cwd(clock_multiplier),
                directory / "dut.log",
                native_environment(
                    save=directory / "dut-own.state" if full_boot else None,
                    disc=disc,
                    trace=True,
                    ram_trace=True,
                    cold_prefix=not full_boot,
                ),
                timeout=boot_cache.BOOT_TIMEOUT_SECONDS * clock_multiplier,
                exit_codes=(0,) if full_boot else (1,),
            ),
            lambda: None
            if producer is not None
            else run(
                [
                    "node",
                    "--import",
                    str(sync_helper),
                    "--import",
                    str(video_helper),
                    str(ROOT / "tools/bbc/reference.mjs"),
                    str(configuration),
                ],
                ROOT,
                directory / "reference.log",
                timeout=boot_cache.BOOT_TIMEOUT_SECONDS,
            ),
        )
    trace = reference / "video-transactions.tsv"
    writes = compare_events(reference_events(trace, {"ram"}), dut_events(dut / "ram-writes.tsv"))
    video = compare_events(
        reference_events(trace, {"crtc", "ula"}), dut_events(dut / "bus.tsv", video=True)
    )
    summary = json.loads((dut / "summary.json").read_text())
    metadata = json.loads((reference / "video-transactions.json").read_text())
    sync = json.loads((reference / "raw-vsync.json").read_text())
    sound = compare_events(
        (
            (int(c) * 16, int(v))
            for c, v in map(str.split, (reference / "psg.tsv").read_text().splitlines())
        ),
        (tuple(map(int, line.split())) for line in (dut / "psg.tsv").read_text().splitlines()),
    )
    audio = (dut / "audio.u8").read_bytes()
    reference_audio = gzip.decompress((reference / "audio.u8.gz").read_bytes())
    frames = list(map(int, (dut / "frames.tsv").read_text().split()))
    transaction_count = sum(1 for _ in trace.open())
    if producer is not None:
        _, current_producer = original_capture(reference_receipt, cycles, keys, observers)
        if producer != current_producer:
            raise ValueError("Reused original cold observations changed during DUT execution")
    checks = {
        "complete": summary["completed"] is full_boot
        and (full_boot or summary.get("capture_scope") == "cold-prefix")
        and summary["restored_own_boot_cycles"] == 0
        and summary["duration_cycles"] == sync["final_cycle"] * 16 == metadata["final_cycle"] * 16
        and metadata["initial_cycle"] == sync["initial_cycle"] == 16
        and transaction_count == metadata["transactions"]
        and len(audio) == len(reference_audio) == summary["duration_cycles"] // 64 - 4
        and len(frames) == summary["frames"]
        and sound["dut_events"] == summary["psg_writes"]
        and sync["observed_audio"] is True,
        "ram_writes": writes["passed"],
        "video_writes": video["passed"],
        "sound_events": sound["passed"],
        "digital_audio": audio == reference_audio,
        "raw_vsync": frames == [e["cycle"] * 16 for e in sync["transitions"] if e["level"]],
        "final_ram": (dut / "boot.ram").read_bytes() == (reference / "boot.ram").read_bytes(),
        "unchanged_inputs": before == inputs() and build.native_current(clock_multiplier),
    }
    report = {
        "passed": all(checks.values()),
        "checks": checks,
        "ram_writes": writes,
        "video_writes": video,
        "sound_events": sound,
        "audio_samples": len(audio),
        "vsync_rises": len(frames),
        "master_clock_hz": clock_multiplier * 32000000,
        "requested_cycles": cycles,
        "full_boot": full_boot,
        "reference_directory": str(reference.relative_to(ROOT)),
        "inputs": before,
        "artifacts": build.hashes(
            [*dut.glob("*"), *reference.glob("*"), *directory.glob("*-own.state*")]
        ),
        "scope": "Independent cold executions with the released boot keys up to the requested duration. Full boots alone save and align own snapshots. Exact accepted RAM and CRTC/ULA write clocks, addresses and data; PSG events, all digital sound samples, raw VSYNC rises and final RAM. The explicit public-API reset ULA setup is not a guest bus write.",
        "limits": "No loading RGB comparison or proof over arbitrary inputs. Own-machine snapshots are saved for inspection, never transferred between backends.",
    }
    write_json(directory / "result.json", report)
    print(f"Cold-boot bus comparison: {checks}; {directory}/result.json", flush=True)
    if not report["passed"]:
        raise ValueError("Cold-boot accepted bus writes or outputs differ")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--clock-multiplier", type=int, choices=[1, 3], default=1)
    parser.add_argument("--cycles", type=int, default=boot_cache.BOOT_CYCLES)
    parser.add_argument(
        "--reference-receipt",
        type=Path,
        help="Reuse guarded original CPU observations; execute the DUT cold",
    )
    args = parser.parse_args()
    qualify(args.clock_multiplier, args.cycles, args.reference_receipt)
