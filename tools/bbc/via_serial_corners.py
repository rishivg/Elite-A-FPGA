"""Compare serial mode/access races and PB6-clocked T2 with unchanged MOSIO."""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

from tools.validation.common import digest, write_json

from . import build, via_serial
from .probe import device_rtl

PATTERNS = (0, 0x81, 0xA5, 0xFF)
PERIODS = (0, 1, 3, 15)


def initial(mode, byte, period=3, pulse=False):
    """Use bus writes and a real zero transfer to establish the startup bank."""
    return [
        "0 R RES 0",
        "2 R RES 1",
        "2 W B 1C",
        "1 W A 00",
        *via_serial.clocks(7, 0),
        "2 W D 7F",
        "2 R PA FF",
        "0 R PB FF",
        "0 R CA1 0",
        "0 R CA2 0",
        "0 R CB1 0",
        "0 R CB2 0",
        f"2 W B {(mode << 2) | (32 if pulse else 0):02X}",
        "1 W C 00",
        "1 W E 84",
        "1 W 4 FF",
        "1 W 5 FF",
        "2 W D 7F",
        f"1 W 8 {period:02X}",
        "1 W 9 7F",
        "0 R CB1 0",
        "2 R CB1 0",
        f"1 W A {byte:02X}",
        "2 W CB1 0",
        "0 R CB1 0",
    ]


def step(mode, bit, *, pulse=False):
    if pulse:
        return [
            "1 R PB BF",
            "1 R PB FF",
            "0 W CB1 0",
            "0 W CB2 0",
            "0 R CB1 0",
            f"0 R CB2 {bit}",
            "0 R D 00",
        ]
    if mode in (0, 3, 7):
        return ["1 R CB1 0", f"0 R CB2 {bit}", "1 R CB1 1", "1 W CB2 0", "0 R D 00"]
    return ["1 W CB1 0", "0 W CB2 0", "0 R CB1 0", f"0 R CB2 {bit}", "0 R D 00"]


def programs():
    rows = []
    for before in range(8):
        for after in range(8):
            for byte in PATTERNS:
                for at in (0, 3, 7, 8):
                    rows.extend(initial(before, byte))
                    for i in range(at):
                        rows.extend(step(before, (byte >> (7 - i % 8)) & 1))
                    rows.append(f"1 W B {after << 2:02X}")
                    for i in range(24):
                        rows.extend(step(after, (byte >> (7 - i % 8)) & 1))
                    rows.extend(["1 R A 00", "1 R D 00"])
    yield "mode-transitions", 1024, 198608, rows
    rows = []
    for mode in (1, 4, 5):
        for period in PERIODS:
            for byte in range(256):
                rows.extend(initial(mode, byte, period, pulse=True))
                for i in range((period + 1) * 20 + 16):
                    rows.extend(step(mode, (byte >> (7 - i % 8)) & 1, pulse=True))
                rows.extend(["1 R A 00", "1 R D 00"])
    yield "pulse-serial", 3072, 2972672, rows
    rows = []
    for mode in (3, 7):
        for byte in range(256):
            for at in range(9):
                for operation in ("R", "W"):
                    rows.extend(initial(mode, byte))
                    for i in range(at):
                        rows.extend(step(mode, (byte >> (7 - i % 8)) & 1))
                    rows.extend(
                        [
                            "1 R CB1 0",
                            f"0 R CB2 {(byte >> (7 - at % 8)) & 1}",
                            "1 R CB1 1",
                            f"0 {operation} A 5A",
                        ]
                    )
                    for i in range(12):
                        rows.extend(step(mode, (byte >> (7 - i % 8)) & 1))
                    rows.extend(["1 R A 00", "1 R D 00"])
    yield "edge-sr-access", 9216, 1244672, rows
    rows = []
    for mode in (1, 4, 5):
        for period in PERIODS:
            for byte in PATTERNS:
                rows.extend(initial(mode, byte, period, pulse=True))
                for _ in range((period + 1) * 20 + 16):
                    rows.extend(
                        [
                            "1 R PB BF",
                            "1 R 8 00",
                            "1 R 9 00",
                            "1 R PB FF",
                            "1 R 8 00",
                            "1 R 9 00",
                            "0 W CB1 0",
                            "0 R CB1 0",
                            "0 W CB2 0",
                            "1 R D 00",
                        ]
                    )
                rows.extend(["1 R A 00", "1 R D 00"])
    yield "pulse-counter-access", 48, 65312, rows


def qualify(clock_multiplier=None):
    extra = via_serial.profile_sources(clock_multiplier)
    master_ticks = 1 if clock_multiplier is None else 8 * clock_multiplier
    suffix = "" if clock_multiplier is None else str(32 * clock_multiplier)
    directory = build.BBC / ("via-serial-corners" + suffix)
    directory.mkdir(exist_ok=True)
    base = build.BBC / ("via-serial" + suffix)
    source = via_serial.original_sources(base)
    original, original_inputs = via_serial.compile_original(base, source)
    dut, inputs, dependencies = via_serial.compile_dut(base, source, master_ticks)
    guarded = [
        Path(__file__),
        *dependencies,
        *extra,
        original,
        dut,
        base / "dut/devices.v",
        *(source / name for name in via_serial.PIN),
    ]
    before = build.hashes(guarded)
    started = time.monotonic()
    groups = {}
    for name, cases, observations, rows in programs():
        fixture = directory / (name + ".input.mosio")
        fixture.write_text("\n".join(rows) + "\n")
        fixture_hash = digest(fixture)
        traces = []
        for label, binary in [("original", original), ("dut", dut)]:
            trace = directory / (name + "." + label + ".mosio")
            subprocess.run(
                [str(binary), "-i", str(fixture), "-o", str(trace)],
                check=True,
                capture_output=True,
                timeout=180 * master_ticks,
            )
            traces.append(trace)
        expected, actual = [path.read_bytes() for path in traces]
        count = expected.count(b"\n")
        complete = (
            observations is not None
            and count == observations
            and expected.count(b" R RES 0\n") == cases
            and expected.endswith(b"\n")
            and actual.endswith(b"\n")
        )
        groups[name] = {
            "passed": complete and expected == actual,
            "cases": cases,
            "observations": count,
            "expected_observations": observations,
            "fixture": fixture_hash,
            "reference": digest(traces[0]),
            "dut": digest(traces[1]),
        }
        if fixture_hash != digest(fixture):
            raise ValueError("Serial corner fixture changed during execution")
        print(
            f"VIA serial {name}: {cases} cases; {count} observations; "
            f"{'PASS' if groups[name]['passed'] else 'FAIL'}",
            flush=True,
        )
    if (
        before != build.hashes(guarded)
        or not build.generation_current()
        or device_rtl(via_serial.MODULES) != (base / "dut/devices.v").read_text()
    ):
        raise ValueError("Serial corner sources changed during comparison")
    receipt = {
        "passed": all(group["passed"] for group in groups.values()),
        "groups": groups,
        "inputs": inputs,
        "original_inputs": original_inputs,
        "artifacts": before,
        "original_commit": via_serial.COMMIT,
        "master_edges_per_host_period": master_ticks,
        "clock_hz": 4000000 * master_ticks,
        "elapsed_seconds": time.monotonic() - started,
        "scope": "Unchanged original MOSIO driver and MOS latch model; original bus/pin operations and complete timestamped outputs. All mode pairs at four byte patterns and four offsets; every byte in external read/write races at all nine transfer offsets; every byte in PB6 serial modes 1/4/5 at T2 periods 0/1/3/15; extra real T2 low/high register reads across pulse underflow and reload. Each backend resets and executes its own device. No state injection or fitted offset.",
        "limits": "Finite legal PHI2/PHI1 sequences with input changes at four-MHz host boundaries. Dense profiles execute every intervening master edge: tick/read accept on the final high-phase edge and retained writes on the first PHI1 edge. Arbitrary changes within a host period, held-CS sub-cycle access, whole-machine clock distribution, metastability and physical acceptance are not established. Full game equivalence remains open.",
    }
    write_json(directory / "result.json", receipt)
    if not receipt["passed"]:
        raise ValueError("Serial corner traces differ or are incomplete; all traces retained")
    return receipt


if __name__ == "__main__":
    qualify()
