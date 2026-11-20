"""Retain a completed audited DUT cold boot in that same machine's cache."""

from __future__ import annotations

import argparse
import fcntl
import json
import shutil
import tempfile
from pathlib import Path

from tools.validation.common import ROOT, digest, write_json

from . import boot_cache, build
from .boot_reuse import copy_boot_file, destination_snapshot, install_boot_files
from .compare import game_state


def retain_dut(receipt):
    receipt = Path(receipt).resolve()
    report = json.loads(receipt.read_text())
    if (
        report.get("passed") is not True
        or report.get("full_boot") is not True
        or report.get("requested_cycles") != boot_cache.BOOT_CYCLES
        or not report.get("checks")
        or not all(report["checks"].values())
    ):
        raise ValueError("Only a completed passing cold-boot audit can retain an own DUT state")
    multiplier = {32000000: 1, 96000000: 3}.get(report.get("master_clock_hz"))
    if multiplier is None:
        raise ValueError("Unknown audited machine clock")
    boot_cache.configure(clock_multiplier=multiplier)
    inputs = boot_cache.inputs("dut")
    if (
        not build.native_current(multiplier)
        or inputs["native"] != report["inputs"]["dut"]
        or any(
            report["inputs"]["reference"]["files"].get(name) != sha
            for name, sha in inputs["files"].items()
        )
    ):
        raise ValueError("Audited cold boot has different current machine or ROM/disc/key inputs")
    source = receipt.parent
    states = boot_cache.dut_states(source / "dut-own.state")
    for state in states:
        if report["artifacts"].get(str(state.relative_to(ROOT))) != digest(state):
            raise ValueError("Audited own DUT state is missing or changed")
    guarded = {
        **report["artifacts"],
        **report["inputs"]["runner"],
        str(receipt.relative_to(ROOT)): digest(receipt),
        str(Path(__file__).relative_to(ROOT)): digest(Path(__file__)),
    }

    def check_source():
        if (
            guarded != build.hashes(ROOT / name for name in guarded)
            or inputs != boot_cache.inputs("dut")
            or not build.native_current(multiplier)
        ):
            raise ValueError("Audited cold-boot bytes or current machine changed")

    check_source()
    ram = source / "dut/boot.ram"
    status = game_state(ram.read_bytes())
    for field, value in {
        "commander_name": "NEWCOME",
        "credits_tenths": 5000,
        "fuel_tenths_ly": 60,
        "view": 8,
        "docked": True,
    }.items():
        if status[field] != value:
            raise ValueError("Audited own cold boot is not the playable status screen: " + field)
    targets = boot_cache.backends()["dut"]
    with (build.BBC / "boot-reuse.lock").open("a+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        destination = boot_cache.receipt_path()
        watched = [destination, *targets]
        before = destination_snapshot(watched)
        retained = Path(tempfile.mkdtemp(prefix=f"audited-own-boot{multiplier}-", dir=build.BBC))
        previous = retained / "previous"
        for path in watched:
            relative = path.relative_to(ROOT)
            if before[str(relative)] is not None:
                copy_boot_file(path, previous / relative, before[str(relative)])
        with tempfile.TemporaryDirectory(prefix="audited-boot-stage-", dir=build.BBC) as temporary:
            staging = Path(temporary)
            for state, target in zip(states, targets, strict=True):
                checksum = report["artifacts"][str(state.relative_to(ROOT))]
                copy_boot_file(state, staging / target.relative_to(ROOT), checksum)
            check_source()
            if before != destination_snapshot(watched):
                raise ValueError("Receiving own boot changed while staging")
            output = retained / "boot-dut"
            output.mkdir()
            shutil.copy2(ram, output / "boot.ram")
            with install_boot_files(staging, targets, destination, previous):
                boot_cache.seal(retained, inputs, "dut")
                check_source()
                if not boot_cache.valid("dut"):
                    raise ValueError("Retained audited own boot is not current")
        result = {
            "retained": True,
            "clock_multiplier": multiplier,
            "audit": str(receipt.relative_to(ROOT)),
            "audit_sha256": digest(receipt),
            "guards": guarded,
            "inputs": inputs,
            "states": boot_cache.state_hashes("dut"),
            "previous": before,
            "scope": "Completed audited cold execution of the current DUT; exact own snapshot bytes retained only by that DUT. Reference observations and reference states are not installed into it. Prior own cache bytes are preserved; partial or failed audits are rejected.",
        }
        write_json(retained / "receipt.json", result)
    print(f"Retained audited own {32 * multiplier} MHz boot: {retained}", flush=True)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("receipt", type=Path)
    retain_dut(parser.parse_args().receipt)
