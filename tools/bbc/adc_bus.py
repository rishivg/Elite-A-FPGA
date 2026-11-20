"""NEC-documented ADC bus contract, separate from nominal analogue timing."""

from __future__ import annotations

import hashlib
import json
import subprocess
import urllib.request
from pathlib import Path

from tools.validation.common import ROOT, digest, write_json

from . import build
from .probe import compile_probe, device_rtl

DIRECTORY = build.BBC / "adc-bus"
DATASHEET = build.BBC / "adc-datasheet/nec-upd7002.pdf"
DATASHEET_SHA = "b2c07c0efdaa5e812592b0f5efac5a2024fc8b1a7972aaae8d33001c894e4cc8"
RECEIPT = DIRECTORY / "result.json"
BINARY = DIRECTORY / "VAdc7002"


def files():
    return [
        Path(__file__),
        ROOT / "sim/native/bbc_adc_bus_test.cpp",
        ROOT / "tools/bbc/reference_adc_results.mjs",
        DATASHEET,
    ]


def identity():
    if not build.generation_current() or digest(DATASHEET) != DATASHEET_SHA:
        raise ValueError("ADC generation or pinned NEC datasheet is stale")
    return {
        "sources": build.hashes(files()),
        "rtl": hashlib.sha256(device_rtl(["Adc7002"]).encode()).hexdigest(),
    }


def current():
    try:
        result = json.loads(RECEIPT.read_text())
        return (
            result.get("passed") is True
            and result.get("cases") == 256
            and result.get("observations") == 4352
            and result.get("identity") == identity()
            and result.get("artifacts") == build.hashes([BINARY, DIRECTORY / "devices.v"])
        )
    except (OSError, ValueError, KeyError):
        return False


def prepare():
    if not DATASHEET.exists():
        with urllib.request.urlopen(
            "https://www.farnell.com/datasheets/100687.pdf", timeout=30
        ) as response:
            data = response.read()
        if hashlib.sha256(data).hexdigest() != DATASHEET_SHA:
            raise ValueError("NEC datasheet checksum mismatch")
        DATASHEET.parent.mkdir(parents=True, exist_ok=True)
        DATASHEET.write_bytes(data)
    if current():
        return json.loads(RECEIPT.read_text())
    before = identity()
    inputs = compile_probe(DIRECTORY, "Adc7002", files(), ["Adc7002"])
    result = subprocess.run([str(BINARY)], check=True, capture_output=True, text=True, timeout=60)
    report = json.loads(result.stdout)
    if report != {"passed": True, "cases": 256, "observations": 4352} or before != identity():
        raise ValueError("Incomplete or changed NEC ADC bus corpus")
    report.update(
        identity=before,
        inputs=inputs,
        artifacts=build.hashes([BINARY, DIRECTORY / "devices.v"]),
        authority={
            "url": "https://www.farnell.com/datasheets/100687.pdf",
            "sha256": DATASHEET_SHA,
            "pages": ["8.78", "8.81", "8.82", "8.83", "8.84"],
        },
        scope="Specification-derived bus assertions: every previous/new control pair, both durations, four result quadrants, retained busy status/data, both low-byte addresses, high-byte read acknowledgement and retained data after acknowledgement. This is a datasheet contract check, not a comparison with an independent physical capture.",
        limits="Nominal 4/10 ms conversion and completion-time analogue sampling remain emulator approximations. NEC says duration depends on analogue input, there is no silicon power-on reset, and reads at completion may be erroneous with uncertain EOC; these physical phases and test mode are not certified.",
    )
    write_json(RECEIPT, report)
    if not current():
        raise ValueError("NEC ADC bus receipt did not verify")
    print("NEC ADC bus: 256 cases; 4352 observations; PASS", flush=True)
    return report


if __name__ == "__main__":
    prepare()
