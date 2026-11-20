"""Compare T2 load/PB6 races with the unchanged MOSIO VIA model."""

import os
import subprocess
import time
from pathlib import Path

from tools.validation.common import ROOT, digest, write_json

from . import build, via_cpu_endpoint, via_serial
from .probe import compile_probe, device_rtl

CASES = 1536
OBSERVATIONS = 83968
CORRECT = "assign timer2Tick = (acr[5] ? ((phi1Start && pulseRequest) && (! pulseLoadPending)) : io_tick);"
PREVIOUS = "assign timer2Tick = (acr[5] ? (phi1Start && pulseRequest) : io_tick);"


def program():
    """All low-byte loads, two shift modes, and three pulse/load alignments."""
    for value in range(256):
        for mode in (0, 4):
            for alignment in ("before", "on", "after"):
                yield from [
                    "0 R RES 0",
                    "4 R RES 1",
                    "2 W B 1C",
                    "2 W CB1 0",
                    "2 W A 00",
                    "2 W CB1 0",
                    *via_serial.clocks(7, 0),
                    "2 W CB1 0",
                    "2 R PB FF",
                    f"2 W B {32 | mode << 2:02X}",
                    "2 W CB1 0",
                    f"2 W 8 {value:02X}",
                    "2 W CB1 0",
                ]
                if alignment in {"before", "on"}:
                    yield "0 R PB BF"
                if alignment == "before":
                    yield "2 W CB1 0"
                yield from ["0 W 9 00", "2 W CB1 0"]
                if alignment == "after":
                    yield from ["0 R PB BF", "2 W CB1 0"]
                yield from [
                    "0 R 8 00",
                    "2 W CB1 0",
                    "0 R 9 00",
                    "2 W CB1 0",
                    "0 R D 00",
                    "2 W CB1 0",
                    "0 R PB FF",
                    "2 W CB1 0",
                    "0 R PB BF",
                    "2 W CB1 0",
                    "0 R 8 00",
                    "2 W CB1 0",
                    "0 R 9 00",
                    "2 W CB1 0",
                    "0 R D 00",
                ]


def compile_previous(directory, actual, driver, adapter):
    """Restore precisely the previous counting predicate for a negative control."""
    directory.mkdir(exist_ok=True)
    text = actual.read_text()
    if text.count(CORRECT) != 1:
        raise ValueError("Changed T2 pulse/load fault target")
    rtl = directory / "devices.v"
    rtl.write_text(text.replace(CORRECT, PREVIOUS))
    guarded = [Path(__file__), rtl, driver, adapter]
    before = build.hashes(guarded)
    with (directory / "build.log").open("w") as log:
        subprocess.run(
            [
                "verilator",
                "--cc",
                "--exe",
                "--build",
                "-j",
                os.environ.get("VERILATOR_JOBS", "1"),
                "-Wno-fatal",
                "--top-module",
                "Via6522",
                "-CFLAGS",
                "-O3 -std=c++20 -flto -Werror=return-type -DBBC_VIA_MASTER_TICKS=8 -DBBC_VIA_CPU_ENDPOINT=1",
                "-LDFLAGS",
                "-flto",
                "-MAKEFLAGS",
                "-B OPT_FAST=-O3",
                "--Mdir",
                str(directory),
                str(rtl),
                str(driver),
            ],
            cwd=ROOT,
            stdout=log,
            stderr=subprocess.STDOUT,
            check=True,
            timeout=180,
        )
    binary = directory / "VVia6522"
    if before != build.hashes(guarded):
        raise ValueError("Pulse/load fault sources changed during compilation")
    write_json(directory / "build.json", {"sources": before, "binary": digest(binary)})
    return binary


def qualify():
    extra = via_serial.profile_sources(3)
    directory = build.BBC / "via-pulse-load"
    directory.mkdir(exist_ok=True)
    base = build.BBC / "via-serial32"
    source = via_serial.original_sources(base)
    original, original_inputs = via_serial.compile_original(base, source)
    driver_dir = directory / "driver"
    driver_dir.mkdir(exist_ok=True)
    driver = driver_dir / "via_core_sim.cpp"
    driver.write_bytes((source / "gateware/VIA/via_core_sim.cpp").read_bytes())
    adapter = ROOT / "sim/native/bbc_via_serial_adapter.h"
    alias = driver_dir / "Vvia_core.h"
    alias.write_text('#include "' + str(adapter) + '"\n')
    dependencies = [
        Path(__file__),
        Path(via_cpu_endpoint.__file__),
        Path(via_serial.__file__),
        driver,
        alias,
        adapter,
    ]
    fixture = directory / "input.mosio"
    rows = list(program())
    if len(rows) != OBSERVATIONS or rows.count("0 R RES 0") != CASES:
        raise ValueError("Incomplete pulse/load fixture")
    fixture.write_text("\n".join(rows) + "\n")
    native = []
    inputs = []
    for multiplier in (1, 3):
        path = directory / f"native{32 * multiplier}"
        inputs.append(
            compile_probe(
                path,
                "Via6522",
                dependencies,
                via_serial.MODULES,
                cpp_standard=20,
                cpp_defines={"BBC_VIA_MASTER_TICKS": 8 * multiplier, "BBC_VIA_CPU_ENDPOINT": 1},
            )
        )
        native.append(path / "VVia6522")
    previous = compile_previous(
        directory / "previous", directory / "native32/devices.v", driver, adapter
    )
    guarded = [
        *dependencies,
        *extra,
        fixture,
        original,
        *native,
        previous,
        *(source / name for name in via_serial.PIN),
        directory / "native32/devices.v",
        directory / "native96/devices.v",
        directory / "previous/devices.v",
    ]
    before = build.hashes(guarded)
    started = time.monotonic()
    traces = []
    for label, binary in [
        ("original", original),
        ("clock32", native[0]),
        ("clock96", native[1]),
        ("previous", previous),
    ]:
        trace = directory / (label + ".mosio")
        with (directory / (label + ".log")).open("w") as log:
            subprocess.run(
                [str(binary), "-i", str(fixture), "-o", str(trace)],
                check=True,
                stdout=log,
                stderr=subprocess.STDOUT,
                timeout=180,
            )
        traces.append(trace)
    comparisons = [
        via_cpu_endpoint.compare_traces(traces[0], p, OBSERVATIONS, CASES) for p in traces[1:]
    ]
    stable = (
        before == build.hashes(guarded)
        and build.generation_current()
        and device_rtl(via_serial.MODULES) == (directory / "native32/devices.v").read_text()
    )
    negative = comparisons[-1]
    detected = (
        negative["differences"] > 0
        and negative["non_interrupt_observations"] == OBSERVATIONS
        and negative["cases"] == CASES
    )
    receipt = {
        "passed": stable and all(p["passed"] for p in comparisons[:2]) and detected,
        "inputs_unchanged": stable,
        "sources": before,
        "inputs": inputs,
        "original_inputs": original_inputs,
        "original_commit": via_serial.COMMIT,
        "cases": CASES,
        "observations": OBSERVATIONS,
        "periods": [{"clock_hz": hz, **p} for hz, p in zip((32000000, 96000000), comparisons[:2])],
        "negative_control": {"name": "previous-pulse-load", "detected": detected, **negative},
        "artifacts": build.hashes(
            [
                *traces,
                *directory.glob("*.log"),
                directory / "previous/build.json",
                directory / "previous/build.log",
            ]
        ),
        "elapsed_seconds": time.monotonic() - started,
        "scope": "All 256 T2 low loads, ordinary and shift-mode-4 counting, PB6 fall before/on/after high-phase load; later genuine pulse retained. Unchanged original MOSIO reference, complete timestamped bus/pin/IRQ comparisons; ordinary reset and accesses, no state injection; all master edges at both actual clock rates.",
        "limits": "Finite legal CPU endpoint histories; original gate model independently specifies these pulse/load semantics. Does not establish arbitrary sub-cycle GPIO, warm reset, all VIA state, or universal game equivalence.",
        "universal_via_equivalence": False,
        "universal_game_equivalence": False,
    }
    write_json(directory / "result.json", receipt)
    print(
        f"VIA pulse/load: {CASES} cases; {OBSERVATIONS} observations per clock; {receipt['passed']}",
        flush=True,
    )
    if not receipt["passed"]:
        raise ValueError("Pulse/load comparison failed; all traces retained")
    return receipt


if __name__ == "__main__":
    qualify()
