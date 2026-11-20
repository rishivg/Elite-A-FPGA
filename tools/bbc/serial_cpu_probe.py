"""Execute shared test firmware on the actual machine and original BBC CPU/devices."""

import json
import re
import shutil
import subprocess
import time
from pathlib import Path

from tools.validation.common import ROOT, digest, source_root, write_json
from tools.validation.setup import verify

from . import build, bus_binding_proof, capture, cycle_reference


def firmware(delay=0, baud=0, framing=5):
    """Normal ROM code exercises status, IRQ entry, aliases and absent devices."""
    rom = bytearray([0xEA] * 16384)
    program = bytearray()

    def emit(*values):
        program.extend(values)

    def store(value, address):
        emit(0xA9, value, 0x8D, address & 255, address >> 8)

    def observe(address, output):
        emit(0xAD, address & 255, address >> 8, 0x8D, output & 255, output >> 8)

    emit(0x78, 0xD8, 0xA2, 0xFF, 0x9A)  # SEI, CLD, LDX #FF, TXS
    store(0, 0x0200)
    store(3, 0xFE08)
    store(baud, 0xFE10)
    control = (framing << 2) | 1
    store(control, 0xFE08)
    observe(0xFE08, 0x0210)
    for _ in range(delay):
        emit(0x24, 0x00)  # Three-cycle BIT shifts every interrupt polling phase.
    store(0xA5, 0xFE09)
    observe(0xFE08, 0x0211)
    store(control | 0x20, 0xFE08)
    emit(0x58)  # CLI; independently scheduled completion must enter IRQ.
    emit(0xAD, 0x00, 0x02, 0xF0, 0xFB)
    emit(0x78)
    observe(0xFE08, 0x0212)
    observe(0xFE17, 0x0213)  # Read serial ULA alias: selects RS-423.
    observe(0xFE0E, 0x0214)
    store(control | 0x20, 0xFE0A)
    emit(0x58)
    for _ in range(16):
        emit(0xEA)
    emit(0x78)
    observe(0x0200, 0x0215)
    store(0x5A, 0xFE0F)
    observe(0xFE0C, 0x0216)
    store(14, 0xFE34)
    observe(0x8000, 0x0217)
    store(15, 0xFE3C)
    observe(0x8000, 0x0218)
    store(14, 0xFE3A)  # Hole must leave the selected BASIC bank unchanged.
    observe(0x8000, 0x0219)
    for index, address in enumerate(
        (0xFE80, 0xFE88, 0xFE90, 0xFE98, 0xFE2F, 0xFE20, 0xFE18, 0xFE1C, 0xFEF8, 0xFC04)
    ):
        observe(address, 0x0221 + index)
    store(0xA6, 0x0220)
    halt = 0xC000 + len(program)
    emit(0x4C, halt & 255, halt >> 8)
    if len(program) >= 0x200:
        raise ValueError("Firmware collides with its IRQ routine")
    rom[: len(program)] = program
    # IRQ saves A, counts the event, disables transmit IRQ, restores A, RTI.
    irq = bytes((0x48, 0xEE, 0x00, 0x02, 0xA9, control, 0x8D, 0x08, 0xFE, 0x68, 0x40))
    rom[0x200 : 0x200 + len(irq)] = irq
    rom[0x3FFA:0x4000] = bytes((0x00, 0xC2, 0x00, 0xC0, 0x00, 0xC2))
    return bytes(rom)


def qualify():
    verify()
    if (
        not all(build.native_current(multiplier) for multiplier in (1, 3))
        or not cycle_reference.current()
    ):
        raise ValueError("Build both actual machine clocks and the independent CPU first")
    directory = build.BBC / "serial-cpu-probe"
    directory.mkdir(exist_ok=True)
    helper = ROOT / "tools/bbc/reference_serial_cpu.mjs"
    dependencies = [
        Path(__file__),
        ROOT / "tools/bbc/bus_binding_proof.py",
        helper,
        ROOT / "tools/bbc/reference_cycle.mjs",
        cycle_reference.BINARY,
        *sorted((source_root("jsbeeb") / "src").rglob("*.js")),
    ]
    before = {
        "sources": build.hashes(dependencies),
        "models": {str(m): build.native_inputs(m) for m in (1, 3)},
        "binaries": build.hashes([build.native_binary(m) for m in (1, 3)]),
    }
    results = []
    for case, (delay, baud, framing) in enumerate(
        (d, b, f) for d in range(8) for b in (0, 1) for f in (0, 5)
    ):
        case_dir = directory / f"case{case}"
        case_dir.mkdir(exist_ok=True)
        image = firmware(delay, baud, framing)
        declared = case_dir / "firmware.rom"
        declared.write_bytes(image)
        original = case_dir / "original.tsv"
        cycles = 12000
        result = subprocess.run(
            [
                "node",
                str(helper),
                str(source_root("jsbeeb")),
                str(cycle_reference.BINARY),
                str(declared),
                str(cycles),
                str(original),
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        (case_dir / "original.log").write_text(result.stdout + result.stderr)
        if result.returncode:
            raise ValueError("Original serial CPU firmware failed; inspect " + str(case_dir))
        metadata = json.loads(result.stdout.splitlines()[-1])
        expected = [tuple(map(int, row.split())) for row in original.read_text().splitlines()]
        if metadata != {"final_cycle": cycles, "writes": len(expected)} or not any(
            a == 0x0220 and data == 0xA6 for _, a, data in expected
        ):
            raise ValueError("Original firmware did not complete its interrupt and register checks")
        for multiplier in (1, 3):
            work = case_dir / f"clock{multiplier}"
            work.mkdir(exist_ok=True)
            source = build.native_cwd(multiplier)
            top = "BbcMachine" if multiplier == 1 else "BbcMachine96"
            model = source / (top + ".v")
            copied = []
            for name in re.findall(r'\$readmem[bh]\(\s*"([^"]+)"', model.read_text()):
                destination = work / name
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes((source / name).read_bytes())
                copied.append(destination)
                if name == top + ".v_toplevel_rom.bin":
                    values = destination.read_text().splitlines()
                    values[:16384] = [f"{byte:08b}" for byte in image]
                    destination.write_text("\n".join(values) + "\n")
            shutil.copyfile(build.BBC / "disc.ssd", work / "disc.ssd")
            input_hashes = build.hashes([declared, *copied, work / "disc.ssd"])
            binary = build.native_binary(multiplier)
            binary_hash = digest(binary)
            started = time.perf_counter()
            # These short component integration probes run directly, without
            # consuming the long gameplay capture queue. All master edges run.
            result = subprocess.run(
                [str(binary), str(cycles * 16), "-", "-", str(work)],
                cwd=work,
                env=capture.native_environment(ram_trace=True, cold_prefix=True),
                capture_output=True,
                text=True,
                timeout=120,
            )
            (work / "run.log").write_text(result.stdout + result.stderr)
            actual = [
                tuple(map(int, row.split()))
                for row in (work / "ram-writes.tsv").read_text().splitlines()
            ]
            differences = sum(a != b for a, b in zip(expected, actual)) + abs(
                len(expected) - len(actual)
            )
            passed = (
                result.returncode == 1
                and differences == 0
                and binary_hash == digest(binary)
                and input_hashes == build.hashes([declared, *copied, work / "disc.ssd"])
            )
            results.append(
                {
                    "case": case,
                    "clock_multiplier": multiplier,
                    "passed": passed,
                    "differences": differences,
                    "writes": len(actual),
                    "seconds": time.perf_counter() - started,
                    "firmware_sha256": digest(declared),
                    "binary_sha256": binary_hash,
                    "input_files": input_hashes,
                }
            )
            print(
                f"Serial CPU case {case}, {multiplier * 32} MHz: {passed}; {differences} differences",
                flush=True,
            )
            if not passed:
                raise ValueError("Actual CPU/device integration differs; inspect " + str(work))
    # Restore only the old fixed-value ACIA behavior in a private RTL copy.
    # The same normally executed firmware must detect the missing transmit IRQ.
    negative_dir = directory / "legacy-serial-negative"
    negative_dir.mkdir(exist_ok=True)
    actual_text = (build.BBC / "BbcMachine.v").read_text()
    serial = bus_binding_proof.module(actual_text, "BbcSerial")
    legacy = (
        serial[: serial.index(");") + 2]
        + """
always @(*) io_result = io_address ? 8'h00 : 8'h08;
assign io_irq = 1'b0;
endmodule
"""
    )
    model = negative_dir / "legacy-serial.v"
    model.write_text(actual_text.replace(serial, legacy, 1))
    native = negative_dir / "native"
    native.mkdir(exist_ok=True)
    binary = native / "VBbcMachine"
    model_inputs = build.hashes(
        [
            model,
            ROOT / "rtl/bbc/BbcCpu.v",
            build.BBC / "ArletCpuEnabled.v",
            ROOT / "third_party/6502/ALU.v",
            ROOT / "sim/native/bbc.cpp",
            ROOT / "sim/native/bbc_audio.h",
            ROOT / "sim/native/bbc_frame_hash.h",
        ]
    )
    recipe = "savable; -O3 -std=c++17 -flto; legacy serial fault only"
    cache = native / "build.json"
    saved = json.loads(cache.read_text()) if cache.exists() else {}
    if (
        saved.get("inputs") != model_inputs
        or saved.get("recipe") != recipe
        or not binary.exists()
        or saved.get("binary") != digest(binary)
    ):
        with (native / "build.log").open("w") as log:
            subprocess.run(
                [
                    "verilator",
                    "--cc",
                    "--exe",
                    "--build",
                    "-j",
                    "1",
                    "--savable",
                    "-Wno-fatal",
                    "--top-module",
                    "BbcMachine",
                    "-CFLAGS",
                    "-O3 -std=c++17 -flto",
                    "-LDFLAGS",
                    "-flto -lcrypto",
                    "-MAKEFLAGS",
                    "OPT_FAST=-O3",
                    "--Mdir",
                    str(native),
                    str(model),
                    str(ROOT / "rtl/bbc/BbcCpu.v"),
                    str(build.BBC / "ArletCpuEnabled.v"),
                    str(ROOT / "third_party/6502/ALU.v"),
                    str(ROOT / "sim/native/bbc.cpp"),
                ],
                cwd=ROOT,
                stdout=log,
                stderr=subprocess.STDOUT,
                check=True,
                timeout=180,
            )
        if model_inputs != build.hashes([ROOT / name for name in model_inputs]):
            raise ValueError("Negative model inputs changed during compilation")
        write_json(cache, {"inputs": model_inputs, "recipe": recipe, "binary": digest(binary)})
    work = negative_dir / "work"
    work.mkdir(exist_ok=True)
    for source in (directory / "case0/clock1").glob("*.bin"):
        shutil.copyfile(source, work / source.name)
    shutil.copyfile(build.BBC / "disc.ssd", work / "disc.ssd")
    negative_binary = digest(binary)
    result = subprocess.run(
        [str(binary), str(12000 * 16), "-", "-", str(work)],
        cwd=work,
        env=capture.native_environment(ram_trace=True, cold_prefix=True),
        capture_output=True,
        text=True,
        timeout=120,
    )
    (work / "run.log").write_text(result.stdout + result.stderr)
    expected = (directory / "case0/original.tsv").read_text().splitlines()
    actual = (work / "ram-writes.tsv").read_text().splitlines()
    negative_detected = (
        result.returncode == 1
        and [tuple(map(int, row.split())) for row in actual]
        != [tuple(map(int, row.split())) for row in expected]
        and negative_binary == digest(binary)
    )
    stable = before == {
        "sources": build.hashes(dependencies),
        "models": {str(m): build.native_inputs(m) for m in (1, 3)},
        "binaries": build.hashes([build.native_binary(m) for m in (1, 3)]),
    }
    report = {
        "passed": stable and negative_detected and all(r["passed"] for r in results),
        "inputs": before,
        "inputs_unchanged": stable,
        "results": results,
        "negative_control": {
            "name": "legacy-fixed-acia-no-irq",
            "detected": negative_detected,
            "input_files": model_inputs,
            "binary_sha256": negative_binary,
        },
        "artifacts": build.hashes(
            [
                p
                for p in directory.rglob("*")
                if p.is_file()
                and p.name != "result.json"
                and (
                    "native" not in p.relative_to(directory).parts
                    or p.name in {"VBbcMachine", "build.json", "build.log"}
                )
            ]
        ),
        "scope": "32 normal firmware runs at both physical master clocks. Independent original pin-cycle CPU with original BBC peripherals vs the actual production machine. Every RAM write address, value and accepted timestamp matches, including IRQ stack/RTI, completion polling phases, framing/baud, serial and ROM/FDC aliases and unimplemented reads. Only declared ROM input differs from Elite-A; no CPU/RAM/device state is injected or shared.",
        "limits": "Finite CPU/device integration corpus, no receive peer or tape image, no universal machine or gameplay proof.",
    }
    write_json(directory / "result.json", report)
    if not report["passed"]:
        raise ValueError("Serial CPU probe inputs changed")
    return report


if __name__ == "__main__":
    qualify()
