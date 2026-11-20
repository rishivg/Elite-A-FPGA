"""Bind every cold-boot MMIO write to the current independently executed FPGA."""

import gzip
import json
import tempfile
from pathlib import Path

from tools.validation.common import ROOT, digest, source_root, write_json
from tools.validation.setup import verify

from . import boot_cache, boot_trace, build, cycle_reference
from .capture import run


def require_complete_boot(report):
    required = {
        "complete",
        "ram_writes",
        "video_writes",
        "sound_events",
        "digital_audio",
        "raw_vsync",
        "final_ram",
        "unchanged_inputs",
    }
    checks = report.get("checks")
    if not (
        report.get("passed") is True
        and report.get("full_boot") is True
        and report.get("requested_cycles") == boot_cache.BOOT_CYCLES
        and isinstance(checks, dict)
        and required.issubset(checks)
        and all(value is True for value in checks.values())
    ):
        raise ValueError("A complete passing independently executed DUT cold boot is required")


def qualify(dut_receipt):
    verify()
    dut_receipt = Path(dut_receipt).resolve()
    old = json.loads(dut_receipt.read_text())
    require_complete_boot(old)
    multiplier = {32000000: 1, 96000000: 3}.get(old.get("master_clock_hz"))
    if multiplier is None:
        raise ValueError("Unknown actual cold-boot master clock")
    boot_cache.configure(clock_multiplier=multiplier)
    if not build.native_current(multiplier) or not cycle_reference.current():
        raise ValueError("Current qualified actual-machine and independent CPU models are required")
    if old["inputs"]["dut"] != build.native_inputs(multiplier):
        raise ValueError("The independently executed DUT cold boot belongs to a different model")
    directory = Path(tempfile.mkdtemp(prefix=f"boot-system-trace{multiplier}-", dir=build.BBC))
    reference = directory / "reference"
    config = directory / "reference.json"
    keys, disc = build.BBC / "boot.keys", build.BBC / "disc.ssd"
    write_json(
        config,
        {
            **boot_cache.REFERENCE_OPTIONS,
            "cycle_library": str(cycle_reference.BINARY),
            "jsbeeb": str(source_root("jsbeeb")),
            "disc": str(disc),
            "keys": str(keys),
            "cycles": boot_cache.BOOT_CYCLES,
            "output": str(reference),
            "save_state": str(directory / "reference-own.state"),
        },
    )
    observers = [
        ROOT / "tools/bbc" / name
        for name in (
            "reference_boot_sync.mjs",
            "reference_boot_video_trace.mjs",
            "reference_boot_bus.mjs",
        )
    ]
    helpers = [
        Path(__file__),
        ROOT / "tools/bbc/boot_trace.py",
        ROOT / "tools/bbc/capture.py",
        *observers,
        config,
        keys,
        disc,
        dut_receipt,
    ]
    guards = {**old["artifacts"], **old["inputs"]["reused_original"]["outputs"]}

    def inputs():
        return {
            "dut": build.native_inputs(multiplier),
            "reference": boot_cache.inputs("reference"),
            "helpers": build.hashes(helpers),
            "existing_capture": {name: digest(ROOT / name) for name in guards},
        }

    before = inputs()
    if before["existing_capture"] != guards:
        raise ValueError("Existing independently executed cold observations changed")
    print(f"Independent original cold MMIO capture: {directory}", flush=True)
    run(
        [
            "node",
            *[arg for path in observers for arg in ("--import", str(path))],
            str(ROOT / "tools/bbc/reference.mjs"),
            str(config),
        ],
        ROOT,
        directory / "reference.log",
        timeout=boot_cache.BOOT_TIMEOUT_SECONDS,
    )
    dut = dut_receipt.parent / "dut"
    source_reference = ROOT / old["reference_directory"]
    metadata = json.loads((reference / "cold-mmio.json").read_text())
    original_summary = json.loads((reference / "summary.json").read_text())
    summary = json.loads((dut / "summary.json").read_text())
    with (reference / "cold-mmio.tsv").open() as stream:
        writes = boot_trace.compare_events(
            ((int(c) * 16, int(a), int(d)) for c, a, d in map(str.split, stream)),
            boot_trace.dut_events(dut / "bus.tsv"),
        )
    # Extra observation must leave the existing original execution unchanged,
    # including every RAM/video/scroll write and the complete sampled sound.
    names = (
        "video-transactions.tsv",
        "video-final.ram",
        "video-initial.ram",
        "raw-vsync.json",
        "boot.ram",
        "psg.tsv",
    )
    independent_repeat = {
        name: digest(reference / name) == digest(source_reference / name) for name in names
    }
    independent_repeat["audio.u8.gz"] = gzip.decompress(
        (reference / "audio.u8.gz").read_bytes()
    ) == gzip.decompress((source_reference / "audio.u8.gz").read_bytes())
    checks = {
        "complete": metadata["ready"] is True
        and metadata["initial_cycle"] == 16
        and metadata["final_cycle"] * 16 == summary["duration_cycles"]
        and original_summary["cycles"] == metadata["final_cycle"]
        and writes["reference_events"] == metadata["writes"],
        "all_mmio_writes": writes["passed"],
        "independent_original_repeat": all(independent_repeat.values()),
        "unchanged_inputs": before == inputs() and build.native_current(multiplier),
    }
    verify()
    report = {
        "passed": all(checks.values()),
        "checks": checks,
        "inputs": before,
        "all_mmio_writes": writes,
        "original_repeat": independent_repeat,
        "dut_receipt": str(dut_receipt.relative_to(ROOT)),
        "master_clock_hz": multiplier * 32000000,
        "reference_directory": str(reference.relative_to(ROOT)),
        "artifacts": build.hashes([*reference.glob("*"), directory / "reference.log", config]),
        "scope": "Every accepted CPU write at FE00..FFFF during the complete independently executed cold boot, at exact common-master clock/address/data. Current actual DUT capture is unchanged and model-bound. A fresh original CPU/device execution repeats all prior RAM/video/scroll writes, final RAM, digital sound and raw sync without state injection. All observations are retained.",
        "limits": "Completed cold boot, not arbitrary CPU histories, full gameplay or physical hardware. Existing DUT boot is reused only with exact current native-model inputs and unchanged complete artifacts.",
    }
    write_json(directory / "result.json", report)
    print(
        f"Cold accepted MMIO writes: {report['passed']}; {writes['reference_events']} observations",
        flush=True,
    )
    if not report["passed"]:
        raise ValueError("Cold MMIO differs; independent complete observations retained")
    return report


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dut_receipt", type=Path)
    qualify(parser.parse_args().dut_receipt)
