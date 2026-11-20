"""Check production audio interval capture against the original sound chip."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from tools.validation.common import ROOT, digest, source_root, write_json

from . import build
from .probe import compile_probe, device_rtl

DIRECTORY = build.BBC / "audio-interval"
BINARY = DIRECTORY / "VPsg"


def build_probe():
    return compile_probe(
        DIRECTORY,
        "Psg",
        [
            Path(__file__),
            ROOT / "sim/native/bbc_audio.h",
            ROOT / "tools/bbc/reference_audio.mjs",
            ROOT / "sim/native/bbc_audio_test.cpp",
            ROOT / "tools/bbc/reference_psg.mjs",
        ],
        ["Psg"],
    )


def qualify():
    inputs = build_probe()
    subprocess.run([str(BINARY), str(DIRECTORY / "dut.u8")], check=True, timeout=30)
    script = r"""
import fs from 'node:fs';
import {pathToFileURL} from 'node:url';
const [root,observer,output]=process.argv.slice(2);
const {SoundChip}=await import(pathToFileURL(root+'/src/soundchip.js'));
const {observeDigitalAudio}=await import(pathToFileURL(observer));
const chip=new SoundChip(()=>{}),chunks=[];
// Same controlled silent startup; writes are ordinary chip protocol.
for(const byte of [0x9f,0xbf,0xdf,0xff])chip.poke(byte);
observeDigitalAudio(chip,c=>chunks.push(Buffer.from(c.levels)));
const writes=[[0,0x80],[64,0],[127,0x90],[256,0x9f],[289,0x90],[417,0x9f],[511,0x90],[640,0x9f]];
let previous=0;
for(const [cycle,byte] of writes){chip.advance((cycle-previous)/16);chip.poke(byte);previous=cycle;}
chip.advance((1024-previous)/16);
fs.writeFileSync(output,Buffer.concat(chunks));
"""
    subprocess.run(
        [
            "node",
            "--input-type=module",
            "-",
            str(source_root("jsbeeb")),
            str(ROOT / "tools/bbc/reference_audio.mjs"),
            str(DIRECTORY / "reference.u8"),
        ],
        input=script,
        text=True,
        check=True,
        timeout=30,
    )
    expected = (DIRECTORY / "reference.u8").read_bytes()
    actual = (DIRECTORY / "dut.u8").read_bytes()
    differences = [i for i, (r, d) in enumerate(zip(expected, actual)) if r != d]
    result = {
        "passed": len(expected) == len(actual) == 16 and not differences,
        "samples": len(actual),
        "different_samples": differences,
        "clock_hz": 32000000,
        "sample_hz": 500000,
        "fixture": "zero-period tone, silent startup, volume writes before/at/after interval boundaries",
        "inputs": inputs,
        "reference_sha256": digest(DIRECTORY / "reference.u8"),
        "dut_sha256": digest(DIRECTORY / "dut.u8"),
    }
    write_json(DIRECTORY / "result.json", result)
    if not result["passed"]:
        raise ValueError(
            "BBC audio interval capture disagrees with original sound chip: " + str(result)
        )
    return result


def qualify_generators():
    """A declared common clock/reset fixture; expected pins come from JS code.

    Both machines use muted zero-period registers and a first edge at 2 us.
    The original generator's counter phase is unchanged. This fixture does
    not qualify a physical SN power-on phase.
    """
    inputs = build_probe()
    fixture = {
        "cycles": 512000,
        "writes": [
            [8, 0x86],
            [40, 4],
            [72, 0x90],
            [104, 0xA3],
            [136, 1],
            [168, 0xB3],
            [200, 0xCD],
            [232, 0],
            [264, 0xD8],
            [296, 0xE4],
            [328, 0xF2],
            [12808, 0xE5],
            [28808, 0xE4],
            [48008, 0xE6],
            [70408, 0xE7],
            [96008, 0xCF],
            [118408, 0xE3],
            [144008, 0xE0],
            [163208, 0xE4],
            [195208, 0xE4],
            [240008, 0xFF],
            [249608, 0xF0],
            [256008, 0xE4],
            [320008, 0xC5],
            [384008, 0xE7],
        ],
    }
    write_json(DIRECTORY / "generators.json", fixture)
    (DIRECTORY / "generators.tsv").write_text("".join(f"{c} {b}\n" for c, b in fixture["writes"]))
    subprocess.run(
        [
            str(BINARY),
            str(DIRECTORY / "generators-dut.u8"),
            str(DIRECTORY / "generators.tsv"),
            str(fixture["cycles"]),
        ],
        check=True,
        timeout=30,
    )
    subprocess.run(
        [
            "node",
            str(ROOT / "tools/bbc/reference_psg.mjs"),
            str(source_root("jsbeeb")),
            str(DIRECTORY / "generators.json"),
            str(DIRECTORY / "generators-reference.u8"),
        ],
        check=True,
        timeout=30,
    )
    results = {}
    for name, suffix in [("levels", ""), ("pins", ".pins")]:
        expected = (DIRECTORY / ("generators-reference.u8" + suffix)).read_bytes()
        actual = (DIRECTORY / ("generators-dut.u8" + suffix)).read_bytes()
        different = sum(r != d for r, d in zip(expected, actual)) + abs(len(expected) - len(actual))
        results[name] = {
            "samples": len(actual),
            "different_samples": different,
            "passed": len(expected) == len(actual) == 8000 and different == 0,
        }
    result = {
        "passed": all(r["passed"] for r in results.values()),
        "captures": results,
        "inputs": inputs,
        "fixture": fixture,
        "startup": "Common declared 250 kHz divider phase; no game phase fitting",
        "coverage": "Three tones; attenuation; white/periodic noise at all rates; linked tone changes; same-control rewrites",
    }
    write_json(DIRECTORY / "generators-result.json", result)
    if not result["passed"]:
        raise ValueError("PSG generators differ from original sound chip: " + str(results))
    return result


def qualify_bus(clock_multiplier=1, reset_only=False, linked_zero=False):
    if reset_only and linked_zero:
        raise ValueError("Choose one sound-bus fixture")
    """Stable WE windows against the unchanged original chip and scheduler."""
    if clock_multiplier not in {1, 3}:
        raise ValueError("Unsupported sound-bus clock multiplier")
    profile = {}
    if clock_multiplier == 3:
        from . import video96

        video96.generate()
        machine = video96.DIRECTORY / "BbcMachine96.v"
        generated = machine.read_text()
        modules = ["BbcAudio", "BbcSoundBus", "Psg"]
        selected = {
            m[1]: m[0]
            for m in re.finditer(r"^module\s+(\w+)\b[\s\S]*?^endmodule\b", generated, re.M)
        }
        if (
            device_rtl(modules)
            != "`timescale 1ns/1ps\n" + "\n\n".join(selected[name] for name in modules) + "\n"
        ):
            raise ValueError("The 96 MHz machine does not use the qualified audio modules")
        profile = build.hashes([machine])
    name = "sound-reset" if reset_only else "sound-linked-zero" if linked_zero else "sound-bus"
    directory = build.BBC / (name if clock_multiplier == 1 else name + "96")
    binary = directory / "VBbcSoundBusTest"
    inputs = compile_probe(
        directory,
        "BbcSoundBusTest",
        [
            Path(__file__),
            ROOT / "sim/native/bbc_audio.h",
            ROOT / "tools/bbc/reference_audio.mjs",
            ROOT / "sim/native/bbc_sound_bus_test.v",
            ROOT / "sim/native/bbc_sound_bus_test.cpp",
            ROOT / "tools/bbc/reference_sound_bus.mjs",
        ],
        ["BbcAudio", "BbcSoundBus", "Psg"],
        cpp_defines={"BBC_MASTER_MULTIPLIER": clock_multiplier},
        parameters={"MASTER_MULTIPLIER": clock_multiplier},
    )
    events = [
        (0, 1, 0, 127),
        (16, 1, 1, 0x86),
        (80, 1, 1, 0x84),
        (240, 1, 1, 0x86),
        (464, 1, 1, 0x81),
        (640, 1, 0, 4),
        (1040, 1, 1, 0x90),
        (1552, 1, 1, 0xA3),
        (2064, 1, 1, 1),
        (2576, 1, 1, 0xB3),
        (3088, 1, 1, 0xCD),
        (3600, 1, 1, 0),
        (4112, 1, 1, 0xD8),
        (4624, 1, 1, 0xE4),
        (5136, 1, 1, 0xF2),
        (12816, 1, 1, 0xE5),
        (28816, 1, 1, 0xE4),
        (48016, 1, 1, 0xE6),
        (70416, 1, 1, 0xE7),
        (96016, 1, 1, 0xCF),
        (118416, 1, 1, 0xE3),
        (144016, 1, 1, 0xE0),
        (163216, 1, 1, 0xE4),
        (195216, 1, 1, 0xE4),
        (240016, 1, 1, 0xFF),
        (249616, 1, 1, 0xF0),
        (256016, 1, 1, 0xE4),
        (320016, 1, 1, 0xC5),
        (384016, 1, 1, 0xE7),
        (400016, 0, 1, 0xFF),
        (401040, 1, 1, 0xF0),
        (401104, 0, 1, 0xF0),
        (402064, 1, 1, 0xFF),
        (402288, 0, 1, 0xFF),
    ]
    if reset_only:
        events = events[:1]
    elif linked_zero:
        events = events[:1] + [
            (cycle, 1, 1, byte)
            for cycle, byte in [
                (1040, 0xC0),
                (1552, 0),
                (2064, 0xE3),
                (2576, 0xF0),
                (32016, 0xE7),
                (64016, 0xC1),
                (128016, 0xC0),
                (192016, 0xCF),
                (192528, 63),
                (300016, 0xE3),
                (340016, 0xC0),
                (340528, 0),
            ]
        ]
    expected_writes = 1 if reset_only else 13 if linked_zero else 27
    fixture = directory / "events.tsv"
    fixture.write_text("".join(" ".join(map(str, e)) + "\n" for e in events))
    guarded = [
        binary,
        fixture,
        directory / "devices.v",
        *[ROOT / name for name in inputs["sources"]],
    ]
    before = build.hashes(guarded)
    subprocess.run(
        [str(binary), str(fixture), str(directory / "dut.u8"), "512000"], check=True, timeout=30
    )
    subprocess.run(
        [
            "node",
            str(ROOT / "tools/bbc/reference_sound_bus.mjs"),
            str(source_root("jsbeeb")),
            str(fixture),
            str(directory / "reference.u8"),
            "512000",
        ],
        check=True,
        timeout=30,
    )
    captures = {}
    for name, suffix in [("levels", ""), ("pins", ".pins"), ("writes", ".writes")]:
        expected = (directory / ("reference.u8" + suffix)).read_bytes()
        actual = (directory / ("dut.u8" + suffix)).read_bytes()
        captures[name] = {
            "passed": expected == actual,
            "reference_sha256": digest(directory / ("reference.u8" + suffix)),
            "dut_sha256": digest(directory / ("dut.u8" + suffix)),
        }
        if name == "writes":
            captures[name]["events"] = len(actual.splitlines())
            captures[name]["passed"] &= len(actual.splitlines()) == expected_writes
        else:
            captures[name]["samples"] = len(actual)
            captures[name]["passed"] &= len(actual) == 8000
            captures[name]["different_samples"] = sum(
                a != b for a, b in zip(actual, expected)
            ) + abs(len(actual) - len(expected))
    if (
        not build.generation_current()
        or before != build.hashes(guarded)
        or profile != build.hashes([ROOT / path for path in profile])
    ):
        raise ValueError("Sound-bus inputs changed during comparison")
    result = {
        "passed": all(c["passed"] for c in captures.values()),
        "master_clock_hz": 32000000 * clock_multiplier,
        "actual_machine_profile": profile,
        "artifacts": before,
        "inputs": inputs,
        "captures": captures,
        "fixture": events,
        "startup": "Declared Model B reset pins: WE active and data 127; muted tones and common divider; timestamps identify accepted master edges, as in the machine runner. No game phase fitting.",
        "coverage": (
            "Initial seven-microsecond window starts at reset release; exact first acceptance, all digital samples and PSG pins."
            if reset_only
            else "Linked periodic and white noise; tone-2 zero entry/exit, retained counter phase and all samples through ordinary sound-bus writes."
            if linked_zero
            else "Initial active reset pins; latest byte while pending; held WE; repeated byte; early cancel; data/WE changes exactly at scheduled acceptance; later fresh window"
        ),
        "physical_acceptance": "Original stable-window approximation; physical chip phase remains unqualified",
    }
    write_json(directory / "result.json", result)
    if not result["passed"]:
        raise ValueError(
            "BBC sound bus differs from original stable-window interface: " + str(captures)
        )
    return result


if __name__ == "__main__":
    for check in [qualify, qualify_generators, qualify_bus]:
        result = check()
        print(f"{check.__name__}: PASS; receipt contains capture counts and source hashes")
    qualify_bus(clock_multiplier=3)
    print("qualify_bus (96 MHz): PASS; receipt contains capture counts and source hashes")
    for multiplier in (1, 3):
        qualify_bus(clock_multiplier=multiplier, reset_only=True)
        print(f"qualify_bus reset ({multiplier * 32} MHz): PASS")
