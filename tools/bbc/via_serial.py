"""VIA serial transfers against an unchanged MOS latch implementation."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import time
import urllib.request
from pathlib import Path

from tools.validation.common import ROOT, digest, write_json

from . import build
from .probe import compile_probe, device_rtl

COMMIT = "c81b61b73108b19e7176d05c5a4f91ae96e36995"
PIN = {
    "LICENSE": "253ad3f89603e728abfa60c36fbcaf8225cf55c1eab12725f19fb3d74d647f3a",
    "gateware/VIA/README.md": "c5f9bb04324f301c8ff5dad316097d8cef1b5d59a98f6d904cb39f7857119566",
    "gateware/VIA/redip_via.sv": "20aad025f2047a249b3b75fd8ce17de4d212014cae321c27093e9a0d584c2e40",
    "gateware/VIA/via_control.sv": "c8e240465854afdd5a22e7edec14250f8cbf7eaca5fa5c97817e8cb48b7a7762",
    "gateware/VIA/via_core.sv": "e287a48ebb32e8f903f224d5bae7c058dccc473ae55aa9f7a10236770acb7fb4",
    "gateware/VIA/via_edgedet.sv": "b6c54512f2cfa0478215a35105b1a51b58e245a31a3073e142cd9189b2800b71",
    "gateware/VIA/via_interrupt.sv": "19c853dded8e4784da4320c6ac7dd1c8f2903ee031683b1318411cca4be383c8",
    "gateware/VIA/via_io.sv": "dbd9afce9db955db7df6c0337b4b3f0432970130960e89d9111029fd233bacf8",
    "gateware/VIA/via_pkg.sv": "d5685c9fc27d53bfbb53e8271e11ed1c09dd1d9699f1e8d2d91be30293a672ee",
    "gateware/VIA/via_ports.sv": "2b0c5e69e22a9c23c0bfa5d0372ffd8e70dbeef65df1c505e0a05bee508feae5",
    "gateware/VIA/via_serial.sv": "3f00564d2f1cd387794fd1da423f99ff98075ecb8db942c014946265d46fc7d5",
    "gateware/VIA/via_timers.sv": "181b4e3e1167cef8f70e0f82086922313af0f755b4b47ab1508cfda78d004c7d",
    "gateware/VIA/via_core_sim.cpp": "5acda4b2eeb507c80668084ba43948c04bd450a38d3f7c5be5008ec921adbf2d",
}
MODULES = ["Via6522", "ViaControlPins", "ViaSerial"]


def original_sources(directory):
    root = directory / "sources"
    for name, expected in PIN.items():
        path = root / name
        if not path.exists():
            url = f"https://raw.githubusercontent.com/daglem/reDIP-CIA/{COMMIT}/{name}"
            with urllib.request.urlopen(url, timeout=30) as response:
                data = response.read()
            if hashlib.sha256(data).hexdigest() != expected:
                raise ValueError("Original MOS latch source checksum: " + name)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        if digest(path) != expected:
            raise ValueError("Original MOS latch source changed: " + name)
    return root


def external_program():
    """Actual pin and bus operations; no state writes or expected values."""
    lines = []
    for mode in [3, 7]:
        for data in range(256):
            for initial in [0, 1]:
                for extra in [0, 1]:
                    lines += [
                        "0 R RES 0",
                        "2 R RES 1",
                        "2 R PA FF",
                        "0 R PB FF",
                        "0 R CA1 0",
                        "0 R CA2 0",
                        f"0 R CB1 {initial}",
                        "0 R CB2 0",
                        f"2 W B {mode << 2:02X}",
                        "1 W C 00",
                        "1 W E 84",
                        "1 W D 7F",
                        f"1 W A {data if mode == 7 else 0:02X}",
                    ]
                    for bit in range(8):
                        if mode == 7:
                            lines += ["2 R CB1 1", "2 R CB1 0", "1 W CB2 0"]
                        else:
                            lines += [
                                "2 R CB1 0",
                                f"0 R CB2 {(data >> (7 - bit)) & 1}",
                                "2 R CB1 1",
                                "1 W CB2 0",
                            ]
                    lines += ["2 R D 00"]
                    if extra:
                        if mode == 7:
                            lines += ["2 R CB1 1", "2 R CB1 0", "1 W CB2 0"]
                        else:
                            lines += [
                                "2 R CB1 0",
                                f"0 R CB2 {(data & 1) ^ 1}",
                                "2 R CB1 1",
                                "1 W CB2 0",
                            ]
                    lines += ["1 R A 00", "1 R D 00"]
    return "\n".join(lines) + "\n"


def clocks(mode, byte, count=8):
    """Clock actual external pins; output transfers circulate their own data."""
    rows = []
    for bit in range(count):
        if mode == 7:
            rows += ["2 R CB1 1", "2 R CB1 0", "1 W CB2 0"]
        else:
            rows += [
                "2 R CB1 0",
                f"0 R CB2 {(byte >> (7 - bit % 8)) & 1}",
                "2 R CB1 1",
                "1 W CB2 0",
            ]
    return rows


def restart_program(disabled):
    rows = []
    for mode in [0] if disabled else [3, 7]:
        for byte in range(256):
            for initial in [0, 1]:
                for pcr in [0xC0, 0xE0] if disabled else [0]:
                    rows += [
                        "0 R RES 0",
                        "2 R RES 1",
                        "2 R PA FF",
                        "0 R PB FF",
                        "0 R CA1 0",
                        "0 R CA2 0",
                        f"0 R CB1 {initial}",
                        "0 R CB2 0",
                        f"2 W B {mode << 2:02X}",
                        f"1 W C {pcr:02X}",
                        "1 W E 84",
                        "1 W D 7F",
                        f"1 W A {byte:02X}",
                    ]
                    if disabled:
                        # Drive CB2 through PCR: the inactive drive bank itself is
                        # not an input level, and differs among software models.
                        rows += clocks(0, 0x96)
                    else:
                        rows += clocks(mode, byte) + ["2 R D 00", "1 W D 04", "2 R D 00"]
                        rows += clocks(mode, byte ^ 255, 9)
                    rows += ["2 R D 00", "1 R A 00", "1 R D 00"]
    return "\n".join(rows) + "\n"


def internal_program():
    rows = []
    for mode in [1, 2, 4, 5, 6]:
        for byte in range(256):
            for period in [0] if mode in [2, 6] else [0, 1, 3, 15]:
                rows += ["0 R RES 0", "2 R RES 1", "2 W B 1C", "1 W A 00"]
                # SR/CB2 have no defined silicon reset. Execute a real transfer
                # to establish the output before observing an internal clock.
                rows += clocks(7, 0)
                rows += [
                    "2 W D 7F",
                    "2 R PA FF",
                    "0 R PB FF",
                    "0 R CA1 0",
                    "0 R CA2 0",
                    "0 R CB1 0",
                    "0 R CB2 0",
                    f"2 W B {mode << 2:02X}",
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
                # Release CS through a complete PHI2 before read polling. The
                # retained-write latch is tested separately by the adapter.
                for cycle in range(40 if mode in [2, 6] else 40 * (period + 2)):
                    rows += [
                        "1 W CB1 0",
                        "0 W CB2 0",
                        "0 R CB1 0",
                        f"0 R CB2 {(byte >> (7 - cycle % 8)) & 1}",
                        "0 R D 00",
                    ]
                rows += ["1 R A 00", "1 R D 00"]
    return "\n".join(rows) + "\n"


def program():
    return external_program() + restart_program(True) + restart_program(False) + internal_program()


def compile_original(directory, source):
    binary = directory / "original-native/Vvia_core"
    sources = [Path(__file__), *(source / name for name in PIN)]
    inputs = {
        "sources": build.hashes(sources),
        "verilator": subprocess.check_output(["verilator", "--version"], text=True).strip(),
        "compiler": subprocess.check_output(["c++", "--version"], text=True).splitlines()[0],
        "recipe": "Unmodified via_core and public MOSIO driver; -O3 -std=c++20 -flto; header includes only; declared original 4 MHz logic clock",
    }
    try:
        saved = json.loads((directory / "original-build.json").read_text())
    except (OSError, ValueError):
        saved = {}
    if (
        not binary.is_file()
        or saved.get("inputs") != inputs
        or saved.get("binary") != digest(binary)
    ):
        with (directory / "original-build.log").open("w") as log:
            subprocess.run(
                [
                    "verilator",
                    "--cc",
                    "--exe",
                    "--build",
                    "-j",
                    "2",
                    "-Wno-fatal",
                    "-Wno-CASEINCOMPLETE",
                    "--top-module",
                    "via_core",
                    "-y",
                    str(source / "gateware/VIA"),
                    "-CFLAGS",
                    "-O3 -std=c++20 -flto -include charconv -include ranges -include sstream",
                    "-LDFLAGS",
                    "-flto",
                    "-MAKEFLAGS",
                    "-B OPT_FAST=-O3",
                    "--Mdir",
                    str(binary.parent),
                    str(source / "gateware/VIA/via_pkg.sv"),
                    str(source / "gateware/VIA/via_core.sv"),
                    str(source / "gateware/VIA/via_core_sim.cpp"),
                ],
                cwd=ROOT,
                stdout=log,
                stderr=subprocess.STDOUT,
                check=True,
                timeout=120,
            )
        if inputs["sources"] != build.hashes(sources):
            raise ValueError("Original serial source changed during compilation")
        write_json(directory / "original-build.json", {"inputs": inputs, "binary": digest(binary)})
    return binary, inputs


def profile_sources(clock_multiplier):
    """Bind dense clocks to the actual generated machine's device modules."""
    if clock_multiplier not in {None, 1, 3}:
        raise ValueError("Unsupported serial master-clock profile")
    if clock_multiplier != 3:
        return []
    from . import video96

    video96.generate()
    machine = video96.DIRECTORY / "BbcMachine96.v"
    generated = machine.read_text()
    current = device_rtl(MODULES)
    for name in MODULES:
        pattern = rf"^module\s+{name}\b[\s\S]*?^endmodule\b"
        a, b = [re.search(pattern, text, re.M) for text in [current, generated]]
        if a is None or b is None or a[0] != b[0]:
            raise ValueError("Serial probe differs from actual 96 MHz module: " + name)
    return [machine, ROOT / "tools/bbc/video96.py"]


def compile_dut(directory, source, master_ticks=1):
    driver = directory / "dut/driver"
    driver.mkdir(parents=True, exist_ok=True)
    # Copy exact driver bytes to an adapter include directory, never altering
    # any original file or placing an adapter header beside original sources.
    copied = driver / "via_core_sim.cpp"
    copied.write_bytes((source / "gateware/VIA/via_core_sim.cpp").read_bytes())
    alias = driver / "Vvia_core.h"
    alias.write_text('#include "' + str(ROOT / "sim/native/bbc_via_serial_adapter.h") + '"\n')
    dependencies = [Path(__file__), copied, alias, ROOT / "sim/native/bbc_via_serial_adapter.h"]
    inputs = compile_probe(
        directory / "dut",
        "Via6522",
        dependencies,
        MODULES,
        cpp_standard=20,
        cpp_defines={"BBC_VIA_MASTER_TICKS": master_ticks},
    )
    return directory / "dut/VVia6522", inputs, dependencies


def qualify(clock_multiplier=None):
    extra = profile_sources(clock_multiplier)
    master_ticks = 1 if clock_multiplier is None else 8 * clock_multiplier
    directory = build.BBC / (
        "via-serial" if clock_multiplier is None else f"via-serial{32 * clock_multiplier}"
    )
    directory.mkdir(parents=True, exist_ok=True)
    source = original_sources(directory)
    original, original_inputs = compile_original(directory, source)
    dut, inputs, dependencies = compile_dut(directory, source, master_ticks)
    fixture = directory / "input.mosio"
    fixture.write_text(program())
    guarded = [
        *dependencies,
        *extra,
        original,
        dut,
        fixture,
        directory / "dut/devices.v",
        *(source / name for name in PIN),
    ]
    before = build.hashes(guarded)
    started = time.monotonic()
    reference_trace = directory / "original.mosio"
    dut_trace = directory / "dut.mosio"
    for binary, output in [(original, reference_trace), (dut, dut_trace)]:
        subprocess.run(
            [str(binary), "-i", str(fixture), "-o", str(output)],
            check=True,
            capture_output=True,
            text=True,
            timeout=30 * master_ticks,
        )
    expected = reference_trace.read_bytes()
    actual = dut_trace.read_bytes()
    # MOSIO emits one newline-terminated record per observation, with fixed
    # space-delimited operation/register fields. Count in the byte stream to
    # avoid allocating and splitting millions of records; compare every byte.
    observations = expected.count(b"\n")
    if not expected.endswith(b"\n") or observations != 4657664 or expected.count(b" R A ") != 7680:
        raise ValueError("Incomplete independent serial observations")
    if (
        before != build.hashes(guarded)
        or not build.generation_current()
        or device_rtl(MODULES) != (directory / "dut/devices.v").read_text()
    ):
        raise ValueError("Serial inputs changed during comparison")
    passed = expected == actual
    receipt = {
        "passed": passed,
        "cases": 7680,
        "observations": observations,
        "elapsed_seconds": time.monotonic() - started,
        "inputs": inputs,
        "original_inputs": original_inputs,
        "artifacts": before,
        "reference_trace": digest(reference_trace),
        "dut_trace": digest(dut_trace),
        "original_commit": COMMIT,
        "master_edges_per_host_period": master_ticks,
        "clock_hz": 4000000 * master_ticks,
        "groups": {
            "internal_phi2": {"cases": 512, "observations": 128512},
            "internal_t2": {"cases": 3072, "observations": 4302848},
            "external": {"cases": 2048, "observations": 95744},
            "disabled": {"cases": 1024, "observations": 49152},
            "ifr_only": {"cases": 1024, "observations": 81408},
        },
        "coverage": "All five internal-clock modes; every byte; four T2 periods, including minimum period; real loopback of generated CB1 and complete CB2, SR, IFR and IRQ observations. Disabled-mode data shifts with both PCR-driven CB2 levels; IFR-only release without counter rearm in modes 3/7; every byte and both initial CB1 levels. Modes 3/7; every byte; both initial CB1 levels; eight serial clocks and an extra clock after completion; all requested serial pin levels, SR reads, completion/IRQ timestamps and read acknowledgement. Each case actually resets its own device; one process per backend retains all bus operations.",
        "scope": "Unchanged reDIP MOS 6522 latch implementation and its unchanged MOSIO driver, separately compiled against current generated RTL through a bus/pin adapter. Reference state is never injected. The adapter maps accepted one-MHz endpoints before the original driver's CPU interrupt sample; it never fits a trace offset. Only compiler header includes are added to original compilation.",
        "limits": "Declared stable external inputs at four-MHz host boundaries; dense profiles execute all intervening master edges. Tick/read accept on the final high-phase edge; the retained write accepts on the first PHI1 edge, before low-phase latches settle. This does not prove arbitrary pin changes inside a host period or the whole-machine clock distribution. Arbitrary coincident accesses, output-bank mode transitions and physical chip timing have separate obligations. Full game equivalence is not established by this corpus.",
        "source_license": "Unmodified independent reference is CERN-OHL-S-2.0; LICENSE and original notices retained with downloaded source. Production serial counter is a separate endpoint implementation, not a translated latch layout.",
    }
    write_json(directory / "result.json", receipt)
    print(
        f"VIA serial: 7680 cases; 4657664 observations; {'0 differences' if passed else 'FAIL'}",
        flush=True,
    )
    if not passed:
        raise ValueError("Serial traces differ; both complete traces retained")
    return receipt


if __name__ == "__main__":
    qualify()
