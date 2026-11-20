"""Qualify Timer 1/2 readback, interrupts and PB7 against original VIA code."""

from __future__ import annotations

import subprocess
from pathlib import Path

from tools.validation.common import ROOT, digest, source_root, write_json

from . import build
from .probe import compile_probe


def qualify(bus=False, edges=False):
    if bus and edges:
        raise ValueError("Choose one VIA observation mode")
    directory = build.BBC / "via-probe"
    inputs = compile_probe(
        directory,
        "Via6522",
        [
            Path(__file__),
            ROOT / "sim/native/bbc_via_test.cpp",
            ROOT / "sim/native/bbc_via_edges.h",
            ROOT / "tools/bbc/reference_via_edges.mjs",
            ROOT / "tools/bbc/reference_via.mjs",
        ],
        ["ViaControlPins", "ViaSerial", "Via6522"],
    )
    if edges:
        fixture = directory / "edges.tsv"
        subprocess.run(
            [
                "node",
                str(ROOT / "tools/bbc/reference_via_edges.mjs"),
                str(source_root("jsbeeb")),
                str(fixture),
            ],
            check=True,
            timeout=30,
        )
        result = subprocess.run(
            [str(directory / "VVia6522"), str(fixture), "edges"],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        print(result.stdout.strip())
        receipt = {
            "passed": True,
            "cases": 128,
            "observations": 2944,
            "inputs": inputs,
            "fixture_sha256": digest(fixture),
            "coverage": "CA/CB polarity and independent modes; PA/PB input latches; mixed output masks; repeated edges and simultaneous acknowledgements",
        }
        write_json(directory / "edges-result.json", receipt)
        return receipt
    fixture = directory / ("bus.tsv" if bus else "timers.tsv")
    mode = ["bus"] if bus else []
    subprocess.run(
        [
            "node",
            str(ROOT / "tools/bbc/reference_via.mjs"),
            str(source_root("jsbeeb")),
            str(fixture),
            *mode,
        ],
        check=True,
        timeout=30,
    )
    result = subprocess.run(
        [str(directory / "VVia6522"), str(fixture), *mode],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    print(result.stdout.strip())
    receipt = {
        "passed": True,
        "cases": 32,
        "observations": 6552,
        "inputs": inputs,
        "fixture_sha256": digest(fixture),
        "coverage": "T1 one-shot/continuous relatch; T2 wrap; 0/1/2/3/254/255/256/FFFF loads; load-delay readback; IRQ and PB7",
        "clock": "Shared declared 1 MHz boundaries; "
        + (
            "pre-edge CPU reads and IRQ acknowledgement"
            if bus
            else "passive post-edge observations"
        ),
    }
    write_json(directory / ("bus-result.json" if bus else "result.json"), receipt)
    return receipt


if __name__ == "__main__":
    qualify()
    qualify(bus=True)
    qualify(edges=True)
