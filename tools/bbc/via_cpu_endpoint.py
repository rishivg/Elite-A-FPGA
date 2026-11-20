"""Compare serial bus accesses at the actual BBC high-phase CPU endpoint."""

from __future__ import annotations

import argparse
import gzip
import itertools
import subprocess
import time
from pathlib import Path

from tools.validation.common import ROOT, digest, write_json

from . import build, machine_clock_proof, via_serial, via_serial_corners
from .probe import compile_probe, device_rtl


def with_fetches(rows):
    """Release CS for a full PHI2 between register accesses.

    A pin-output observation advances the unchanged driver's deselected bus;
    it does not drive an input or supply expected output. The resulting extra
    clock is executed by both independent devices. This models the intervening
    memory access, rather than continuously selecting VIA through a read after
    a write (which exposes the silicon model's retained-write latch).
    """
    for row in rows:
        yield row
        if len(row.split()[2]) == 1:
            yield "2 W CB1 0"


def compare_traces(original, dut, rows, cases):
    observations = non_interrupts = resets = differences = 0
    first = []
    with original.open("rb") as expected, dut.open("rb") as actual:
        for index, (left, right) in enumerate(itertools.zip_longest(expected, actual)):
            observations += 1
            if left is None or right is None:
                raise ValueError("Incomplete CPU-endpoint serial trace")
            fields = left.split()
            if len(fields) != 4 or not left.endswith(b"\n") or not right.endswith(b"\n"):
                raise ValueError("Malformed CPU-endpoint serial trace")
            non_interrupts += fields[1] != b"I"
            resets += fields[1:] == [b"R", b"RES", b"0"]
            if left != right:
                differences += 1
                if len(first) < 16:
                    first.append([index, left.decode().strip(), right.decode().strip()])
    return {
        "passed": non_interrupts == rows and resets == cases and differences == 0,
        "cases": cases,
        "observations": observations,
        "non_interrupt_observations": non_interrupts,
        "expected_non_interrupt_observations": rows,
        "differences": differences,
        "first_differences": first,
        "raw_original_sha256": digest(original),
        "raw_dut_sha256": digest(dut),
    }


def compress(path):
    """Keep complete traces without retaining their rebuildable raw copies."""
    destination = Path(str(path) + ".gz")
    with path.open("rb") as source, gzip.open(destination, "wb", compresslevel=1) as target:
        while chunk := source.read(1 << 20):
            target.write(chunk)
    path.unlink()
    return destination


def programs():
    yield "all-modes", 7680, None, via_serial.program().splitlines()
    yield from via_serial_corners.programs()


def qualify(multiplier=1):
    if multiplier not in {1, 3}:
        raise ValueError("Unsupported BBC master clock")
    extra = via_serial.profile_sources(multiplier)
    clocks = machine_clock_proof.qualify(multiplier)
    directory = build.BBC / f"via-cpu-endpoint{32 * multiplier}"
    directory.mkdir(exist_ok=True)
    base = build.BBC / f"via-serial{32 * multiplier}"
    source = via_serial.original_sources(base)
    original, original_inputs = via_serial.compile_original(base, source)
    driver = directory / "driver"
    driver.mkdir(exist_ok=True)
    copied = driver / "via_core_sim.cpp"
    copied.write_bytes((source / "gateware/VIA/via_core_sim.cpp").read_bytes())
    alias = driver / "Vvia_core.h"
    adapter = ROOT / "sim/native/bbc_via_serial_adapter.h"
    alias.write_text('#include "' + str(adapter) + '"\n')
    dependencies = [
        Path(__file__),
        ROOT / "tools/bbc/via_serial.py",
        ROOT / "tools/bbc/via_serial_corners.py",
        copied,
        alias,
        adapter,
        *extra,
    ]
    inputs = compile_probe(
        directory / "native",
        "Via6522",
        dependencies,
        via_serial.MODULES,
        cpp_standard=20,
        cpp_defines={"BBC_VIA_MASTER_TICKS": 8 * multiplier, "BBC_VIA_CPU_ENDPOINT": 1},
    )
    dut = directory / "native/VVia6522"
    guarded = [
        *dependencies,
        original,
        dut,
        directory / "native/devices.v",
        *[source / name for name in via_serial.PIN],
        build.BBC / f"machine-clock-proof{multiplier}/result.json",
    ]
    before = build.hashes(guarded)
    started = time.monotonic()
    groups = {}
    artifacts = []
    for name, cases, _, rows in programs():
        fixture = directory / (name + ".input.mosio")
        count = 0
        with fixture.open("w") as stream:
            for row in with_fetches(rows):
                stream.write(row + "\n")
                count += 1
        del rows
        fixture_hash = digest(fixture)
        traces = [directory / (name + "." + label + ".mosio") for label in ("original", "dut")]
        for binary, trace in zip((original, dut), traces):
            with (directory / (trace.name + ".log")).open("w") as log:
                subprocess.run(
                    [str(binary), "-i", str(fixture), "-o", str(trace)],
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    check=True,
                    timeout=180 * multiplier,
                )
        groups[name] = compare_traces(*traces, count, cases)
        if fixture_hash != digest(fixture):
            raise ValueError("CPU-endpoint serial fixture changed")
        groups[name]["fixture_sha256"] = fixture_hash
        artifacts.extend(compress(path) for path in [fixture, *traces])
        print(f"CPU-endpoint serial {32 * multiplier} MHz {name}: {groups[name]}", flush=True)
    checks = {
        "all_groups": all(group["passed"] for group in groups.values()),
        "unchanged_sources": before == build.hashes(guarded),
        "actual_device_binding": build.generation_current()
        and device_rtl(via_serial.MODULES) == (directory / "native/devices.v").read_text(),
        "actual_parent_clocks": clocks["passed"],
    }
    report = {
        "passed": all(checks.values()),
        "checks": checks,
        "groups": groups,
        "inputs": inputs,
        "sources": before,
        "original_inputs": original_inputs,
        "parent_clock_proof": clocks,
        "artifacts": build.hashes(artifacts),
        "elapsed_seconds": time.monotonic() - started,
        "scope": "Actual current VIA modules at the BBC CPU endpoint: accepted reads and writes on the final high-phase master edge. The unchanged original MOS latch model and unchanged public MOSIO driver execute their own reset and legal transfers. A complete deselected PHI2 separates register accesses. All intervening 32 or 96 MHz edges execute; every returned register, pin observation and timestamped IRQ is compared.",
        "limits": "Representative CPU bus sequences, not an execution of the whole CPU or proof that every reachable game path has an intervening fetch. Continuous selection through a read after a write exposes the original retained-write latch; the separate PHI1 adapter covers that bus behavior. Arbitrary sub-host input timing and physical hardware are not established.",
    }
    write_json(directory / "result.json", report)
    if not report["passed"]:
        raise ValueError("CPU-endpoint serial differs; complete traces retained")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--clock-multiplier", type=int, choices=(1, 3), default=1)
    qualify(parser.parse_args().clock_multiplier)
