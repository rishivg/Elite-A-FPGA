"""Compose bounded cold video evidence with the actual bus, scroll and RAM paths."""

import argparse
import json
from pathlib import Path

from tools.validation.common import ROOT, digest, write_json
from tools.validation.setup import verify

from . import boot_cache, build, loading_video


def sealed(path):
    report = json.loads(path.read_text())
    if report.get("passed") is not True:
        raise ValueError("Composition requires passing evidence: " + str(path))
    if "checks" in report and (
        not report["checks"] or any(value is not True for value in report["checks"].values())
    ):
        raise ValueError("Incomplete comparison checks: " + str(path))
    artifacts = report.get("artifacts", {})
    if not artifacts:
        raise ValueError("Composition requires complete artifact hashes: " + str(path))
    if any(digest(ROOT / name) != sha for name, sha in artifacts.items()):
        raise ValueError("Composition artifacts changed: " + str(path))
    return report


def unchanged(hashes):
    if not hashes or any(digest(ROOT / name) != sha for name, sha in hashes.items()):
        raise ValueError("Composition producer or model bytes changed")


def qualify(boot_path, video_path, bus_path):
    verify()
    boot_cache.configure(clock_multiplier=3)
    if not build.native_current(3):
        raise ValueError("Build the current actual 96 MHz whole machine before composition")
    paths = [
        boot_path,
        video_path,
        bus_path,
        build.BBC / "scroll-proof/result.json",
        build.BBC / "ram-proof/result.json",
    ]
    before = build.hashes([Path(__file__), *paths])
    boot, video, bus, scroll, ram = (sealed(path) for path in paths)
    if not (
        boot.get("full_boot") is True
        and boot["requested_cycles"] == boot_cache.BOOT_CYCLES
        and boot["master_clock_hz"] == bus["master_clock_hz"] == 96000000
        and video["requested_cycles"] == boot_cache.BOOT_CYCLES
        and boot["inputs"]["dut"] == bus["inputs"]["dut"] == build.native_inputs(3)
        and boot["inputs"]["reference"]
        == video["inputs"]["reference"]
        == bus["inputs"]["reference"]
        == boot_cache.inputs("reference")
        and bus["dut_receipt"] == str(boot_path.relative_to(ROOT))
        and boot["reference_directory"] == video["reference_directory"]
        and video["inputs"]["machine_binding"] == loading_video.machine_binding()
    ):
        raise ValueError("Cold composition profiles, original streams or actual DUT modules differ")
    unchanged(video["inputs"]["files"])
    unchanged(bus["inputs"]["helpers"])
    unchanged(bus["inputs"]["existing_capture"])
    unchanged(scroll["inputs"])
    unchanged(ram["inputs"])
    unchanged(
        {
            name: sha
            for name, sha in boot["inputs"]["runner"].items()
            if name
            in {
                "tools/bbc/boot_trace.py",
                "build/bbc/full-video/native/VBbcMachine96",
            }
        }
    )
    for component in (scroll, ram):
        if (
            not component["inputs_unchanged"]
            or not component["negative_control_detected"]
            or not all(
                parent["passed"] and parent["negative_control_detected"]
                for parent in component["parent_bindings"]
            )
        ):
            raise ValueError("Incomplete actual-parent component proof")
    # Recompare every accepted write ourselves; previous result flags are not a
    # substitute for checking the newly supplied original MMIO observations.
    from .boot_trace import compare_events, dut_events

    native = boot_path.parent / "dut"
    original = ROOT / bus["reference_directory"]
    with (original / "cold-mmio.tsv").open() as stream:
        writes = compare_events(
            (
                (int(clock) * 16, int(address), int(data))
                for clock, address, data in map(str.split, stream)
            ),
            dut_events(native / "bus.tsv"),
        )
    if writes != bus["all_mmio_writes"] or not writes["passed"]:
        raise ValueError("Accepted MMIO composition differs")
    verify()
    stable = before == build.hashes([Path(__file__), *paths]) and build.native_current(3)
    report = {
        "passed": stable,
        "inputs": before,
        "inputs_unchanged": stable,
        "bounded_cold_video_composition": True,
        "universal_game_equivalence": False,
        "master_clock_hz": 96000000,
        "duration_common_cycles": json.loads((native / "summary.json").read_text())[
            "duration_cycles"
        ],
        "accepted_ram_writes": boot["ram_writes"]["dut_events"],
        "accepted_mmio_writes": writes["dut_events"],
        "video_observations": video["dut"],
        "receipts": [str(path.relative_to(ROOT)) for path in paths],
        "scope": "Composition for this complete declared cold boot: independently executed current FPGA CPU accepted RAM and all MMIO writes match the independent original execution. The exact current video module and ROMs pass every loading RGB dot, fetch and HS/VS observation using those writes and their own power-on RAM. Actual VIA/IC32 scroll and shared-RAM paths satisfy the independently checked contracts and actual parent bindings. No reference state is installed in the FPGA.",
        "limits": "Bounded, compositional cold-boot evidence. No fresh monolithic 96 MHz RGB capture is invented. The standalone renderer's diagnostic memory addresses between fetches, CPU readback during RAM writes and light-pen register contents are outside this RGB/fetch/sync claim. Recorded original JavaScript-versus-C bitmap disagreements remain in the linked loading receipt. Arbitrary gameplay, input histories and physical hardware remain unproved.",
    }
    directory = build.BBC / "cold-video-composition"
    write_json(directory / "result.json", report)
    print(
        f"Bounded actual cold-video composition: {stable}; {writes['dut_events']} MMIO writes",
        flush=True,
    )
    if not stable:
        raise ValueError("Composition source bytes changed")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--boot", type=Path, required=True)
    parser.add_argument("--video", type=Path, required=True)
    parser.add_argument("--bus", type=Path, required=True)
    args = parser.parse_args()
    qualify(args.boot.resolve(), args.video.resolve(), args.bus.resolve())
