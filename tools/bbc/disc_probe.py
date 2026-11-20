"""Qualify the real 8271 host bus, write pipeline and sector readback."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from tools.validation.common import ROOT, digest, source_root, write_json

from . import build
from .probe import compile_probe


def qualify():
    directory = build.BBC / "disc-probe"
    dependencies = [
        Path(__file__),
        ROOT / "sim/native/bbc_disc_test.cpp",
        ROOT / "tools/bbc/reference_disc.mjs",
        ROOT / "tools/bbc/reference_drive.mjs",
    ]
    inputs = compile_probe(directory, "Intel8271", dependencies, ["Intel8271"])
    original = directory / "original.json"
    subprocess.run(
        [
            "node",
            str(ROOT / "tools/bbc/reference_disc.mjs"),
            str(source_root("jsbeeb")),
            str(build.BBC / "disc.ssd"),
            str(original),
            "clocked-fm",
        ],
        check=True,
        timeout=30,
    )
    payload = directory / "payload.tsv"
    result = subprocess.run(
        [
            str(directory / "VIntel8271"),
            str(original) + ".bus.tsv",
            str(build.BBC / "disc.ssd"),
            str(payload),
        ],
        cwd=build.BBC,
        capture_output=True,
        text=True,
        # This budget covers host contention; the fixture still fixes every guest clock.
        timeout=120,
    )
    (directory / "result.log").write_text(result.stdout + result.stderr)
    if result.returncode != 0:
        raise ValueError(
            "Original 8271 host bus differs; retained " + str(directory / "result.log")
        )
    flux = json.loads(Path(str(original) + ".flux.json").read_text())
    # Declared standard FM track: sector 2's header cell 642, payload 667..922.
    expected = [
        [epoch, 3 * 2560 + 2 * 256 + cell - 667, value]
        for epoch, cell, value in flux
        if 667 <= cell <= 922
    ]
    actual = [list(map(int, row.split())) for row in payload.read_text().splitlines()]
    if len(expected) != 256 or actual != expected:
        raise ValueError("Physical WRITE payload timing/address/data differs from original flux")
    if any(b[0] - a[0] != 128 for a, b in zip(expected, expected[1:])):
        raise ValueError("Original WRITE payload is not on the declared FM clock")
    if not build.generation_current():
        raise ValueError("Controller sources changed during disc qualification")
    receipt = {
        "passed": True,
        "inputs": inputs,
        "bus_observations": len(Path(str(original) + ".bus.tsv").read_text().splitlines()),
        "physical_payload_bytes": len(actual),
        "fixture_sha256": digest(Path(str(original) + ".bus.tsv")),
        "payload_sha256": digest(payload),
        "coverage": "READ and WRITE host handshakes; 2-cell WRITE pipeline; physical payload and readback; multi-sector reads; READY confirmation; absent-drive and cold-start errors",
        "clock": "Declared 300 rpm standard FM; public original host/flux API at 2 MHz boundaries; native 32 MHz",
        "limitations": "Host observations do not qualify every NMI edge; raw flux formatting, CRC corruption and nonstandard tracks remain outside this fixture",
    }
    write_json(directory / "result.json", receipt)
    print(result.stdout.strip())
    print("256 physical WRITE cells and written-sector readback match the original controller")
    return receipt


if __name__ == "__main__":
    qualify()
