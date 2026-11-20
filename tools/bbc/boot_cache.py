"""Reuse only unchanged, independently executed own-machine boot snapshots."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

from tools.validation.common import ROOT, digest, write_json

from .build import BBC, native_current, native_inputs
from .compare import game_state


def dut_states(state):
    """The model and lossless raw medium are required to resume this DUT."""
    return [state, Path(str(state) + ".fm"), Path(str(state) + ".ssd"), Path(str(state) + ".rgb")]


STATES = [
    *dut_states(BBC / "boot.state"),
    BBC / "reference-cycle-clocked-fm-shared-reset-bbc-result-bits.state",
]
BACKENDS = {"dut": STATES[:4], "reference": STATES[4:]}
RECEIPT = BBC / "boot-cache.json"
CLOCK_MULTIPLIER = 1
BOOT_CYCLES = 576000000
# A fresh simulator has no trained compiler profile. Allow host contention
# during this 18-second guest boot; the emulated timing remains unchanged.
BOOT_TIMEOUT_SECONDS = 1200
REFERENCE_OPTIONS = {
    "reference_cpu": "cycle",
    "drive_profile": "clocked-fm",
    "power_profile": "shared-reset",
    "adc_profile": "bbc-result-bits",
}


@dataclass(frozen=True)
class Profile:
    """A declared medium owns its cache; capture workers never reconfigure it."""

    disc: Path
    backends: dict
    receipt: Path
    reference_options: dict
    clock_multiplier: int = 1


def media_profile(disc, reference_options):
    directory = BBC / ("boots" if CLOCK_MULTIPLIER == 1 else "full-video/boots") / digest(disc)
    directory.mkdir(parents=True, exist_ok=True)
    dut = directory / "dut.state"
    label = "-".join(reference_options[key] for key in REFERENCE_OPTIONS)
    return Profile(
        disc=disc,
        backends={
            "dut": dut_states(dut),
            "reference": [directory / ("reference-" + label + ".state")],
        },
        receipt=directory / "boot-cache.json",
        reference_options=dict(reference_options),
        clock_multiplier=CLOCK_MULTIPLIER,
    )


def backends(context=None):
    return BACKENDS if context is None else context.backends


def clock_multiplier(context=None):
    return CLOCK_MULTIPLIER if context is None else context.clock_multiplier


def native_valid(context=None):
    multiplier = clock_multiplier(context)
    return native_current() if multiplier == 1 else native_current(multiplier)


def receipt_path(context=None):
    if context is not None:
        return context.receipt
    return RECEIPT if CLOCK_MULTIPLIER == 1 else BBC / "full-video/boot-cache.json"


def disc_path(context=None):
    return BBC / "disc.ssd" if context is None else context.disc


def configure(
    reference_cpu="cycle",
    drive_profile="clocked-fm",
    power_profile="shared-reset",
    adc_profile="bbc-result-bits",
    clock_multiplier=1,
):
    global REFERENCE_OPTIONS, STATES, CLOCK_MULTIPLIER
    if clock_multiplier not in {1, 3}:
        raise ValueError("Unsupported BBC master-clock multiplier")
    if clock_multiplier != CLOCK_MULTIPLIER:
        directory = BBC if clock_multiplier == 1 else BBC / "full-video"
        state = directory / "boot.state"
        BACKENDS["dut"] = dut_states(state)
    CLOCK_MULTIPLIER = clock_multiplier
    REFERENCE_OPTIONS = {
        "reference_cpu": reference_cpu,
        "drive_profile": drive_profile,
        "power_profile": power_profile,
        "adc_profile": adc_profile,
    }
    name = f"reference-{reference_cpu}-{drive_profile}-{power_profile}-{adc_profile}.state"
    BACKENDS["reference"] = [BBC / name]
    STATES = BACKENDS["dut"] + BACKENDS["reference"]


def inputs(backend, context=None):
    options = REFERENCE_OPTIONS if context is None else context.reference_options
    paths = [
        BBC / "boot.keys",
        BBC / "rom.hex",
        disc_path(context),
        ROOT / "tools/validation/reference.lock.json",
    ]
    if context is not None:
        # Actual input bytes and their declaration define the executed boot.
        # Adding another preset does not change this medium or its own state.
        paths.append(context.disc.with_suffix(".json"))
    if backend == "reference":
        paths.extend(
            [
                ROOT / "tools/validation/reference_memory.mjs",
                ROOT / "tools/validation/reference_timing.mjs",
                ROOT / "tools/bbc/reference.mjs",
                ROOT / "tools/bbc/reference_drive.mjs",
                ROOT / "tools/bbc/reference_frame.mjs",
                ROOT / "tools/bbc/reference_inputs.mjs",
                ROOT / "tools/bbc/reference_analogue.mjs",
                ROOT / "tools/bbc/reference_audio.mjs",
                ROOT / "tools/bbc/reference_capture.mjs",
                ROOT / "tools/bbc/reference_adc_results.mjs",
            ]
        )
        if options.get("adc_profile", "original") == "bbc-result-bits":
            from . import adc_results

            if not adc_results.current():
                raise ValueError("Completed ADC result reference is stale or unqualified")
            paths.extend(
                [*adc_results.files(), adc_results.RECEIPT, adc_results.BINARY, adc_results.CORPUS]
            )
        if options["reference_cpu"] == "cycle":
            from . import cycle_reference

            if not cycle_reference.current():
                raise ValueError("Cycle reference is stale or unqualified")
            paths.extend(
                [
                    ROOT / "tools/bbc/reference_cycle.mjs",
                    ROOT / "tools/bbc/reference_events.mjs",
                    cycle_reference.RECEIPT,
                    cycle_reference.BINARY,
                ]
            )
    elif backend != "dut":
        raise ValueError("Unknown boot backend")
    result = {
        "files": {str(p.relative_to(ROOT)): digest(p) for p in paths},
        "boot_cycles": BOOT_CYCLES,
    }
    if backend == "dut":
        # Compiler PGO preserves generated state layout and is activated only
        # after identical capture hashes. native_current() independently checks
        # its binary, counters and qualification receipt. Keep the own boot
        # across that verified optimization, but reject source/tool changes.
        multiplier = clock_multiplier(context)
        result["native"] = native_inputs() if multiplier == 1 else native_inputs(multiplier)
    if backend == "reference":
        result["node"] = subprocess.check_output(["node", "--version"], text=True).strip()
        result["options"] = options
    return result


def state_hashes(backend, context=None):
    return {str(p.relative_to(ROOT)): digest(p) for p in backends(context)[backend]}


def valid(backend=None, context=None):
    if backend is None:
        return all(valid(name, context) for name in backends(context))
    try:
        receipt = json.loads(receipt_path(context).read_text())
        if receipt.get("schema") != 2:
            return False
        saved = receipt.get("backends", {}).get(backend, {})
        return (
            bool(saved.get("independent_execution"))
            and (backend != "dut" or native_valid(context))
            and saved.get("inputs")
            == (inputs(backend) if context is None else inputs(backend, context))
            and saved.get("states") == state_hashes(backend, context)
        )
    except (OSError, ValueError):
        return False


def seal(directory: Path, before: dict, backend, context=None):
    current = inputs(backend) if context is None else inputs(backend, context)
    if current != before or (backend == "dut" and not native_valid(context)):
        raise ValueError("Boot sources changed during execution")
    state = game_state((directory / f"boot-{backend}/boot.ram").read_bytes())
    for field, expected in {
        "commander_name": "NEWCOME",
        "credits_tenths": 5000,
        "fuel_tenths_ly": 60,
        "view": 8,
        "docked": True,
    }.items():
        if state[field] != expected:
            raise ValueError(f"{backend} boot is not the expected playable status screen: {field}")
    try:
        receipt = json.loads(receipt_path(context).read_text())
    except (OSError, ValueError):
        receipt = {}
    if receipt.get("schema") != 2:
        receipt = {"schema": 2, "backends": {}}
    receipt["backends"][backend] = {
        "source_run": str(directory),
        "inputs": before,
        "states": state_hashes(backend, context),
        "independent_execution": "This backend executes original ROMs/disc with physical keys; no cross-machine RAM injection",
    }
    write_json(receipt_path(context), receipt)
