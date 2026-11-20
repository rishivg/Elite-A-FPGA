"""Qualify ADC conversion, restart and VIA completion interrupts independently."""

import re
import subprocess
from pathlib import Path

from tools.validation.common import ROOT, digest, source_root, write_json

from . import build
from .probe import compile_probe, device_rtl

MODULES = ["Adc7002", "ViaControlPins", "ViaSerial", "Via6522"]


def qualify(clock_multiplier=1):
    if clock_multiplier not in {1, 3}:
        raise ValueError("Unsupported ADC probe master clock")
    from .machine_clock_proof import qualify as qualify_clocks

    clock_proof = qualify_clocks(clock_multiplier)
    directory = build.BBC / ("adc-probe96" if clock_multiplier == 3 else "adc-probe")
    extra = []
    if clock_multiplier == 3:
        from . import video96

        video96.generate()
        machine96 = video96.DIRECTORY / "BbcMachine96.v"
        generated96 = machine96.read_text()
        generated32 = device_rtl(MODULES)
        for name in MODULES:
            pattern = rf"^module\s+{name}\b[\s\S]*?^endmodule\b"
            a, b = [re.search(pattern, text, re.M) for text in [generated32, generated96]]
            if a is None or b is None or a[0] != b[0]:
                raise ValueError("ADC probe differs from actual 96 MHz module: " + name)
        extra = [machine96, ROOT / "tools/bbc/video96.py"]
    inputs = compile_probe(
        directory,
        "BbcAdcViaProbe",
        [
            Path(__file__),
            ROOT / "sim/native/bbc_adc_probe.v",
            ROOT / "sim/native/bbc_adc_test.cpp",
            ROOT / "tools/bbc/reference_adc.mjs",
            ROOT / "tools/bbc/reference_adc_results.mjs",
        ],
        MODULES,
    )
    guarded = [
        *[ROOT / path for path in inputs["sources"]],
        *extra,
        directory / "VBbcAdcViaProbe",
        *sorted((source_root("jsbeeb") / "src").rglob("*.js")),
    ]
    before = build.hashes(guarded)
    fixture = directory / "conversions.tsv"
    subprocess.run(
        [
            "node",
            str(ROOT / "tools/bbc/reference_adc.mjs"),
            str(source_root("jsbeeb")),
            str(fixture),
        ],
        check=True,
        timeout=30,
    )
    fixture_hash = digest(fixture)
    result = subprocess.run(
        [str(directory / "VBbcAdcViaProbe"), str(fixture), str(clock_multiplier)],
        capture_output=True,
        text=True,
        # Keep the complete simulated history when other native captures share the host.
        timeout=120 * clock_multiplier,
    )
    print(result.stdout.strip())
    if result.returncode:
        print(result.stderr.strip())
    unchanged = (
        before == build.hashes(guarded)
        and fixture_hash == digest(fixture)
        and build.generation_current()
        and device_rtl(MODULES) == (directory / "devices.v").read_text()
    )
    receipt = {
        "passed": result.returncode == 0 and unchanged,
        "cases": 112,
        "observations": 2000,
        "inputs": inputs,
        "artifacts": before,
        "inputs_unchanged": unchanged,
        "fixture_sha256": fixture_hash,
        "clock_hz": 32000000 * clock_multiplier,
        "clock_multiplier": clock_multiplier,
        "phi2_profile": "BbcMachine: high throughout the first 2 MHz slot, low throughout the second; one-MHz VIA tick at the final high-slot master edge. Every intervening master edge executes.",
        "parent_clock_proof": clock_proof,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "deadline_restart_cases": 80,
        "coverage": "Nominal original conversion durations; all channels; retained data/status fields while busy; restart before/at/after completion; both CB1 polarities, VIA acknowledgement and accepted high-byte ADC acknowledgement",
        "reference_limit": "Original scheduler and VIA with the explicit NEC register profile. Manufacturer pages 8.78 and 8.81–8.84 define bus changes; analogue sampling and value-dependent physical conversion duration are not qualified by the nominal scheduler.",
    }
    write_json(directory / "result.json", receipt)
    if not unchanged:
        raise ValueError("ADC comparison inputs changed")
    result.check_returncode()
    return receipt


if __name__ == "__main__":
    qualify()
