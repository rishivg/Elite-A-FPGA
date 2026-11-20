"""Observe live whole-machine cold RGB without driving any reference state."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from tools.validation.common import ROOT, digest, write_json

from . import boot_cache, build, crtc_registers, loading_video, video96
from .capture import native_environment, run

HEADER = ROOT / "sim/native/bbc_cold_video_observer.h"
DRIVER = ROOT / "sim/native/bbc.cpp"
OBSERVERS = [
    ROOT / "tools/bbc" / name
    for name in (
        "reference_boot_video_trace.mjs",
        "reference_boot_video_pins.mjs",
        "reference_boot_sync.mjs",
        "reference_capture.mjs",
    )
]


def pack_compile_cache(directory):
    """Keep restorable compiler caches without hundreds of MB per control."""
    packed = {}
    for path in sorted(directory.glob("*.gch")):
        sha, size, mode = digest(path), path.stat().st_size, path.stat().st_mode & 0o777
        archive = path.with_suffix(path.suffix + ".gz")
        with path.open("rb") as raw, gzip.open(archive, "wb", compresslevel=1) as output:
            shutil.copyfileobj(raw, output)
        with gzip.open(archive, "rb") as restored:
            if hashlib.file_digest(restored, "sha256").hexdigest() != sha:
                raise ValueError("Compiler cache archive verification failed")
        packed[str(path.relative_to(ROOT))] = {
            "archive": str(archive.relative_to(ROOT)),
            "raw_sha256": sha,
            "raw_bytes": size,
            "raw_mode": mode,
            "archive_sha256": digest(archive),
        }
        path.unlink()
    return packed


def observer_driver(source):
    """Only add const observation hooks to the unmodified ordinary driver."""
    replacements = {
        "        BBC_MACHINE_TYPE dut;": "        BBC_MACHINE_TYPE dut;\n"
        "        BbcColdVideoObserver coldVideo;",
        "            const bool endpointVsync = dut.io_videoVsyncEndpoint;": "            coldVideo.before(master, dut);\n"
        "            const bool endpointVsync = dut.io_videoVsyncEndpoint;",
        "            dut.clk = 1;\n            dut.eval();\n": "            dut.clk = 1;\n            dut.eval();\n"
        "            coldVideo.after(master, dut);\n",
        '        std::ofstream summary(output / "summary.json");': "        coldVideo.finish(output, cycles, capturePrefix);\n"
        '        std::ofstream summary(output / "summary.json");',
    }
    for original, observed in replacements.items():
        if source.count(original) != 1:
            raise ValueError("Ordinary machine observer anchor changed")
        source = source.replace(original, observed)
    return (
        '#include "VBbcMachine96.h"\n'
        "#define BBC_MACHINE_TYPE VBbcMachine96\n"
        "#define BBC_MASTER_MULTIPLIER 3\n"
        '#include "bbc_cold_video_observer.h"\n' + source
    )


def original_inputs(receipt):
    """Check independent producers and dots; inherit no earlier DUT success."""
    report = json.loads(receipt.read_text())
    reference, original = loading_video.original_capture(
        receipt, boot_cache.BOOT_CYCLES, build.BBC / "boot.keys", OBSERVERS
    )
    sources = crtc_registers.independent_sources()
    dependencies = [
        ROOT / "sim/native/bbc_loading_teletext_oracle.c",
        ROOT / "sim/native/bbc_loading_bitmap_oracle.c",
        ROOT / "tools/bbc/crtc_registers.py",
        *[sources / name for name in crtc_registers.PIN],
        receipt.parent / "oracle",
        receipt.parent / "bitmap-oracle",
    ]
    current = build.hashes(dependencies)
    if len(current) != len(dependencies) or any(
        report["inputs"]["files"].get(name) != sha for name, sha in current.items()
    ):
        raise ValueError("Independent video dot producers changed")
    cells = receipt.parent / "teletext-cells.bin.gz"
    bitmap = receipt.parent / "bitmap-pins.bin.gz"
    fixtures = [cells, bitmap, receipt.parent / "oracle.log", receipt.parent / "bitmap.log"]
    outputs = build.hashes(fixtures)
    if len(outputs) != len(fixtures) or any(
        report["artifacts"].get(name) != sha for name, sha in outputs.items()
    ):
        raise ValueError("Independent video dot observations changed")
    metadata = json.loads((reference / "video-pins.json").read_text())
    chip = json.loads((receipt.parent / "oracle.log").read_text().splitlines()[-1])
    pixels = json.loads((receipt.parent / "bitmap.log").read_text().splitlines()[-1])
    slots = metadata["count"]
    if (
        report["checks"].get("original_bitmap_counts") is not True
        or chip["character_samples"] != (slots + 1) // 2
        or pixels["slots"] != slots
        or pixels["bitmap_slots"] + metadata["teletext_slots"] != slots
        or pixels["bitmap_dots"] != pixels["bitmap_slots"] * 8
        or pixels["uncompressed_bytes"] != slots * 8
    ):
        raise ValueError("Independent video dot capture is incomplete")
    return (
        reference / "video-pins.bin.gz",
        cells,
        bitmap,
        metadata,
        {
            "original": original,
            "dot_producers": current,
            "dot_observations": outputs,
            "receipt_sha256": digest(receipt),
        },
    )


def compile_observer(directory, fault=None):
    if not build.native_current(3):
        raise ValueError("Build the current 96 MHz whole machine first")
    directory.mkdir(parents=True, exist_ok=True)
    cpp = directory / "observed-machine.cpp"
    cpp.write_text(observer_driver(DRIVER.read_text()))
    model = video96.DIRECTORY / "BbcMachine96.v"
    if fault:
        text = model.read_text()
        faults = {
            "rgb": ("assign io_rgb = video_io_rgb;", "assign io_rgb = video_io_rgb ^ 3'b001;"),
            "missing-dot": ("assign io_pixel = video_io_pixel;", "assign io_pixel = 1'b0;"),
        }
        old, new = faults[fault]
        if text.count(old) != 1:
            raise ValueError("Whole-machine negative-control output changed")
        model = directory / "BbcMachine96.v"
        model.write_text(text.replace(old, new))
    dependencies = [
        Path(__file__),
        HEADER,
        DRIVER,
        ROOT / "sim/native/bbc_audio.h",
        ROOT / "sim/native/bbc_frame_hash.h",
        ROOT / "sim/native/bbc_fm_media.h",
        ROOT / "sim/native/bbc_disc_medium.h",
        ROOT / "rtl/bbc/BbcCpu.v",
        build.BBC / "ArletCpuEnabled.v",
        ROOT / "third_party/6502/ALU.v",
        cpp,
        model,
    ]

    def inputs():
        return {
            "generation": video96.snapshot(),
            "files": build.hashes(dependencies),
            "verilator": subprocess.check_output(["verilator", "--version"], text=True).strip(),
            "compiler": subprocess.check_output(["c++", "--version"], text=True).splitlines()[0],
            "fault": fault,
            "recipe": "Actual BbcMachine96; ordinary cold driver plus const pin observers; O3 C++17 LTO",
        }

    before = inputs()
    binary = directory / "native/VBbcMachine96"
    with (directory / "build.log").open("w") as output:
        subprocess.run(
            [
                "verilator",
                "--cc",
                "--exe",
                "--build",
                "--savable",
                "-j",
                os.environ.get("VERILATOR_JOBS", "1"),
                "-Wno-fatal",
                "--top-module",
                "BbcMachine96",
                "-CFLAGS",
                "-O3 -std=c++17 -flto -I" + str(ROOT / "sim/native"),
                "-LDFLAGS",
                "-flto -lcrypto -lz",
                "--Mdir",
                str(binary.parent),
                str(model),
                str(ROOT / "rtl/bbc/BbcCpu.v"),
                str(build.BBC / "ArletCpuEnabled.v"),
                str(ROOT / "third_party/6502/ALU.v"),
                str(cpp),
            ],
            cwd=video96.DIRECTORY,
            stdout=output,
            stderr=subprocess.STDOUT,
            check=True,
            timeout=300,
        )
    if before != inputs() or not build.native_current(3):
        raise ValueError("Whole-machine sources changed during observer compilation")
    receipt = {
        "inputs": before,
        "binary_sha256": digest(binary),
        "packed_compile_cache": pack_compile_cache(binary.parent),
    }
    write_json(directory / "build.json", receipt)
    return binary, receipt


def complete(result, cycles, prefix):
    if type(cycles) is not int or cycles <= 256 or cycles % 16:
        return False
    counts = (
        "slots",
        "physical_edges",
        "sync_observations",
        "character_samples",
        "teletext_dots",
        "expected_teletext_dots",
        "bitmap_dots",
        "expected_bitmap_dots",
        "sync_differences",
        "pixel_differences",
        "control_differences",
        "coordinate_differences",
        "rgb_differences",
        "unsupported_edges",
    )
    if any(type(result.get(name)) is not int or result[name] < 0 for name in counts):
        return False
    slots = (cycles - 256) // 16
    return (
        type(cycles) is int
        and cycles > 256
        and cycles % 16 == 0
        and result.get("passed") is True
        and result.get("prefix") is prefix
        and result.get("original_end") is (not prefix)
        and result.get("boundary_complete") is True
        and result.get("slots") == slots
        and result.get("physical_edges") == slots * 48
        and result.get("sync_observations") == slots * 49
        and result.get("character_samples") == (slots + 1) // 2
        and result.get("teletext_dots") == result.get("expected_teletext_dots")
        and result.get("bitmap_dots") == result.get("expected_bitmap_dots")
        and result.get("teletext_dots", 0) // 6 + result.get("bitmap_dots", 0) // 8 == slots
        and result.get("teletext_dots", -1) >= 0
        and result.get("bitmap_dots", -1) >= 0
        and result.get("teletext_dots", -1) % 6 == 0
        and result.get("bitmap_dots", -1) % 8 == 0
        and all(
            result.get(name) == 0
            for name in (
                "sync_differences",
                "pixel_differences",
                "control_differences",
                "coordinate_differences",
                "rgb_differences",
                "unsupported_edges",
            )
        )
    )


def smoke_controls(directory, paths):
    """Bounded instrumentation diagnostics, not a gameplay capture or full boot.

    Each executes only 8192 common clocks (256 microseconds of guest time).
    Faults alter actual parent RTL outputs, never the oracle or observer.
    """
    pins, cells, bitmap = paths
    cases = {}
    for fault in (None, "rgb", "missing-dot"):
        case = directory / (fault or "positive")
        binary, compiled = compile_observer(case, fault)
        env = native_environment(disc=build.BBC / "disc.ssd", cold_prefix=True)
        env.update(
            {
                "BBC_COLD_VIDEO_PINS": str(pins),
                "BBC_COLD_VIDEO_CELLS": str(cells),
                "BBC_COLD_VIDEO_BITMAP": str(bitmap),
            }
        )
        result = subprocess.run(
            [str(binary), "8192", "-", "-", str(case / "dut")],
            cwd=video96.DIRECTORY,
            env=env,
            capture_output=True,
            text=True,
            timeout=15,
        )
        (case / "dut.log").write_text(result.stdout + result.stderr)
        observed = json.loads((case / "dut/cold-video.json").read_text())
        field = "rgb_differences" if fault == "rgb" else "pixel_differences"
        passed = (
            result.returncode == 2
            and observed["passed"] is False
            and observed[field] > 0
            and observed["boundary_complete"] is True
            and "Live cold machine video differs" in result.stderr
            if fault
            else result.returncode == 1 and complete(observed, 8192, True)
        )
        cases[fault or "positive"] = {
            "passed": passed,
            "exit_status": result.returncode,
            "observed": observed,
            "native_build": compiled,
            "artifacts": build.hashes([*case.glob("dut/*"), case / "dut.log"]),
        }
    report = {
        "passed": all(case["passed"] for case in cases.values()),
        "cases": cases,
        "universal_game_equivalence": False,
        "scope": "8192-clock passive-instrumentation smoke; no full boot or gameplay claim",
    }
    write_json(directory / "smoke-controls.json", report)
    if not report["passed"]:
        raise ValueError("Passive whole-machine video instrumentation failed: " + str(directory))
    return directory / "positive/native/VBbcMachine96", cases["positive"]["native_build"], report


def qualify(receipt, cycles=None):
    receipt = Path(receipt).resolve()
    pins, cells, bitmap, metadata, original = original_inputs(receipt)
    endpoint = metadata["final_cycle"] * 16
    prefix = cycles is not None
    cycles = endpoint if cycles is None else cycles
    if type(cycles) is not int or not 256 < cycles < (endpoint + int(not prefix)) or cycles % 16:
        raise ValueError("Cold video duration must contain whole CPU slots within the original")
    directory = Path(tempfile.mkdtemp(prefix="cold-machine-video-", dir=build.BBC))
    binary, compiled, controls = smoke_controls(directory / "controls", (pins, cells, bitmap))
    keys = directory / "keys"
    keys.write_text(
        "".join(
            line + "\n"
            for line in (build.BBC / "boot.keys").read_text().splitlines()
            if int(line.split()[0]) < cycles
        )
    )
    dut = directory / "dut"
    env = native_environment(
        disc=build.BBC / "disc.ssd", trace=True, ram_trace=True, cold_prefix=prefix
    )
    env.update(
        {
            "BBC_COLD_VIDEO_PINS": str(pins),
            "BBC_COLD_VIDEO_CELLS": str(cells),
            "BBC_COLD_VIDEO_BITMAP": str(bitmap),
        }
    )
    before = build.hashes([Path(__file__), HEADER, DRIVER, keys, build.BBC / "disc.ssd", binary])
    write_json(
        directory / "inputs.json",
        {
            "inputs": before,
            "original": original,
            "native_build": compiled,
            "requested_cycles": cycles,
        },
    )
    print(f"Passive live cold video, {cycles} common clocks: {directory}", flush=True)
    try:
        run(
            [str(binary), str(cycles), str(keys), "-", str(dut)],
            video96.DIRECTORY,
            directory / "dut.log",
            env,
            timeout=boot_cache.BOOT_TIMEOUT_SECONDS * 3,
            exit_codes=(1,) if prefix else (0,),
        )
    except BaseException as error:
        write_json(
            directory / "result.json",
            {
                "passed": False,
                "qualified": False,
                "error": str(error),
                "universal_game_equivalence": False,
            },
        )
        raise
    observed = json.loads((dut / "cold-video.json").read_text())
    summary = json.loads((dut / "summary.json").read_text())
    checks = {
        "complete_observations": complete(observed, cycles, prefix),
        "actual_rtl_faults_detected": controls["passed"],
        "own_cold_execution": summary["restored_own_boot_cycles"] == 0
        and summary["duration_cycles"] == cycles
        and summary["completed"] is (not prefix),
        "original_dot_counts": prefix
        or (
            observed["slots"] == metadata["count"]
            and observed["teletext_dots"] == metadata["teletext_slots"] * 6 > 0
            and observed["bitmap_dots"] == (metadata["count"] - metadata["teletext_slots"]) * 8 > 0
        ),
        "unchanged_original": original == original_inputs(receipt)[-1],
        "unchanged_inputs": before == build.hashes([ROOT / name for name in before])
        and build.native_current(3)
        and compiled["inputs"]["generation"] == video96.snapshot()
        and compiled["inputs"]["files"]
        == build.hashes([ROOT / name for name in compiled["inputs"]["files"]]),
    }
    report = {
        "passed": all(checks.values()),
        "checks": checks,
        "observed": observed,
        "requested_cycles": cycles,
        "original": original,
        "inputs": before,
        "native_build": compiled,
        "instrumentation_controls": controls,
        "artifacts": build.hashes([*dut.glob("*"), directory / "dut.log"]),
        "universal_game_equivalence": False,
        "scope": "Finite independent cold machine execution; every expected RGB dot and every HS/VS edge. No reference state or bus write is driven into the DUT. Serializer phase is the declared digital contract, not a physical measurement.",
    }
    write_json(directory / "result.json", report)
    if not report["passed"]:
        raise ValueError("Live cold machine video failed: " + str(directory))
    print("Live cold machine video: PASS " + str(directory), flush=True)
    return directory / "result.json"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--oracle-receipt", type=Path)
    parser.add_argument(
        "--cycles", type=int, help="Optional incomplete cold prefix in 32 MHz units"
    )
    args = parser.parse_args()
    receipt = args.oracle_receipt
    if receipt is None:
        produced = loading_video.qualify(boot_cache.BOOT_CYCLES)
        receipts = {
            (ROOT / name).parent / "result.json"
            for name in produced["artifacts"]
            if name.endswith("/teletext-cells.bin.gz")
        }
        if len(receipts) != 1:
            raise ValueError("Independent video producer omitted its output receipt")
        receipt = receipts.pop()
    qualify(receipt, args.cycles)


if __name__ == "__main__":
    main()
