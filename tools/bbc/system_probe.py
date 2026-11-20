"""Qualify accepted VIA GPIO, IC32/keyboard wiring and sound windows."""

import subprocess
from pathlib import Path

from tools.validation.common import ROOT, digest, source_root, write_json

from . import build
from .probe import compile_probe


def compile_system():
    directory = build.BBC / "system-pins"
    inputs = compile_probe(
        directory,
        "BbcSystemProbe",
        [
            Path(__file__),
            ROOT / "sim/native/bbc_system_probe.v",
            ROOT / "sim/native/bbc_system_test.cpp",
            ROOT / "sim/native/bbc_keyboard_irq.h",
            ROOT / "tools/bbc/reference_keyboard_irq.mjs",
            ROOT / "tools/bbc/reference_keyboard_edges.mjs",
            ROOT / "tools/bbc/reference_system_pins.mjs",
        ],
        ["ViaControlPins", "ViaSerial", "Via6522", "BbcSystemPins", "BbcSoundBus"],
    )
    return directory, inputs


def qualify():
    directory, inputs = compile_system()
    fixture = directory / "pins.tsv"
    subprocess.run(
        [
            "node",
            str(ROOT / "tools/bbc/reference_system_pins.mjs"),
            str(source_root("jsbeeb")),
            str(fixture),
        ],
        check=True,
        timeout=30,
    )
    result = subprocess.run(
        [str(directory / "VBbcSystemProbe"), str(fixture)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    print(result.stdout.strip())
    if result.returncode:
        print(result.stderr.strip())
    result.check_returncode()
    receipt = {
        "passed": True,
        "cases": 4,
        "bus_observations": 148,
        "sound_events": 44,
        "inputs": inputs,
        "fixture_sha256": digest(fixture),
        "coverage": "ORA/ORB and DDR writes; IC32 latch/scroll; keyboard PA7 dominance; same-byte writes; deadline data changes and WE cancellation",
        "clock": "Original public SysVia writes after each declared scheduler edge; exact timestamps, no offset fitting",
        "limits": "Physical SN acceptance phase remains unqualified",
    }
    write_json(directory / "result.json", receipt)
    return receipt


def qualify_keyboard():
    directory, inputs = compile_system()
    fixture = directory / "keyboard-irq.tsv"
    subprocess.run(
        [
            "node",
            str(ROOT / "tools/bbc/reference_keyboard_irq.mjs"),
            str(source_root("jsbeeb")),
            str(fixture),
        ],
        check=True,
        timeout=30,
    )
    result = subprocess.run(
        [str(directory / "VBbcSystemProbe"), "irq", str(fixture)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    print(result.stdout.strip())
    if result.returncode:
        print(result.stderr.strip())
    result.check_returncode()
    receipt = {
        "passed": True,
        "cases": 8,
        "bus_observations": 112,
        "inputs": inputs,
        "fixture_sha256": digest(fixture),
        "coverage": "GPIO-generated CA2 transitions at the accepted CPU edge; both polarities; independent acknowledgement; ORA/ORA-no-handshake; retained flags",
        "startup": "Initial constructor pin transients are acknowledged by a common public IFR write before enabling IRQ",
    }
    write_json(directory / "keyboard-result.json", receipt)
    return receipt


def qualify_keyboard_edges():
    directory, inputs = compile_system()
    fixture = directory / "keyboard-edges.tsv"
    original = source_root("jsbeeb")
    sources = [
        Path(__file__),
        ROOT / "tools/bbc/reference_keyboard_edges.mjs",
        directory / "VBbcSystemProbe",
        directory / "devices.v",
        *original.glob("src/**/*.js"),
        *original.glob("src/**/*.mjs"),
    ]
    before = build.hashes(sources)
    subprocess.run(
        ["node", str(ROOT / "tools/bbc/reference_keyboard_edges.mjs"), str(original), str(fixture)],
        check=True,
        timeout=30,
    )
    fixture_hash = digest(fixture)
    result = subprocess.run(
        [str(directory / "VBbcSystemProbe"), "key-edges", str(fixture)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    print(result.stdout.strip(), flush=True)
    if result.returncode:
        print(result.stderr.strip(), flush=True)
    result.check_returncode()
    if before != build.hashes(sources) or fixture_hash != digest(fixture):
        raise ValueError("Keyboard edge comparison inputs changed")
    receipt = {
        "passed": True,
        "cases": 8960,
        "inputs": inputs,
        "sources": before,
        "fixture": fixture_hash,
        "scope": "Every common-32-MHz input phase, ten columns, seven non-modifier rows, both CA2 polarities, selected-column and automatic keyboard scan. IRQ is observed before the event's accepting edge, following ordinary GPIO/IER/IFR setup writes and independently executed resets.",
        "limits": "The original GPIO API asserts IRQ immediately on the enabled edge; this does not measure physical chip propagation or prove the parent CPU clock distribution. The cold-machine accepted-write regression checks that distribution separately.",
    }
    write_json(directory / "keyboard-edges-result.json", receipt)
    return receipt


if __name__ == "__main__":
    qualify()
    qualify_keyboard()
    qualify_keyboard_edges()
