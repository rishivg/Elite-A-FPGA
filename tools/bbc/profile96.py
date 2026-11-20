"""Train a separate 96 MHz PGO simulator and verify unchanged capture bytes."""

from __future__ import annotations

import json
import os
import platform
import resource
import time
from concurrent.futures import ProcessPoolExecutor
from multiprocessing import get_context
from pathlib import Path

from tools.validation.common import ROOT, digest, write_json

from . import boot_cache, build, profile, system96, video96
from .capture import native_environment, run, scenario_command
from .host_budget import capacity
from .inputs import write_schedule

DIRECTORY = build.BBC / "profile96"
NATIVE = DIRECTORY / "native"
DATA = DIRECTORY / "counters"
RECEIPT = DIRECTORY / "receipt.json"
BINARY = NATIVE / "VBbcMachine96"


def command(mode):
    flags = f"-flto -fprofile-{mode}={DATA}"
    if mode == "use":
        flags += " -Werror=coverage-mismatch -Werror=missing-profile"
    return [
        "verilator",
        "--cc",
        "--exe",
        "-O3",
        "--savable",
        "-Wno-fatal",
        "--top-module",
        "BbcMachine96",
        "-CFLAGS",
        "-O3 -std=c++17 " + flags,
        "-LDFLAGS",
        flags + " -lcrypto",
        "--Mdir",
        str(NATIVE),
        str(video96.DIRECTORY / "BbcMachine96.v"),
        str(ROOT / "rtl/bbc/BbcCpu.v"),
        str(build.BBC / "ArletCpuEnabled.v"),
        str(ROOT / "third_party/6502/ALU.v"),
        str(ROOT / "sim/native/bbc_machine96.cpp"),
    ]


def compile_profile(mode):
    profile.execute(command(mode), ROOT, DIRECTORY / f"{mode}-generate.log", timeout=600)
    supported = system96.configure_clock_header(NATIVE)
    profile.execute(
        [
            "make",
            "-f",
            "VBbcMachine96.mk",
            "-B",
            "-j",
            os.environ.get("VERILATOR_JOBS", "2"),
            "OPT_FAST=-O3",
        ],
        NATIVE,
        DIRECTORY / f"{mode}-compile.log",
        timeout=600,
    )
    return supported


def capture(binary, scenario, name, boot_state):
    directory = DIRECTORY / name
    directory.mkdir(parents=True, exist_ok=False)
    write_schedule(directory, scenario)
    cpu_before = resource.getrusage(resource.RUSAGE_CHILDREN)
    started = time.monotonic()
    run(
        scenario_command(binary, directory, scenario, "capture"),
        video96.DIRECTORY,
        directory / "capture.log",
        native_environment(load=boot_state, trace=True),
        timeout=1800,
    )
    cpu_after = resource.getrusage(resource.RUSAGE_CHILDREN)
    return {
        "hashes": profile.artifacts(directory / "capture"),
        "cpu_seconds": cpu_after.ru_utime
        + cpu_after.ru_stime
        - cpu_before.ru_utime
        - cpu_before.ru_stime,
        "queued_wall_seconds": time.monotonic() - started,
    }


def qualify(cases, boot_state):
    """Run independent captures in separate processes for per-child CPU accounting."""
    binaries = {"baseline": system96.BINARY, "optimized": BINARY}
    workers = min(capacity(), len(cases) * len(binaries))
    started = time.monotonic()
    with ProcessPoolExecutor(max_workers=workers, mp_context=get_context("spawn")) as pool:
        pending = {
            name: {
                label: pool.submit(capture, binary, scenario, f"{name}/{label}", boot_state)
                for label, binary in binaries.items()
            }
            for name, scenario in sorted(cases.items(), key=lambda item: -item[1]["duration_us"])
        }
        verification = []
        for name, scenario in cases.items():
            pair = {label: task.result() for label, task in pending[name].items()}
            if pair["baseline"]["hashes"] != pair["optimized"]["hashes"]:
                raise ValueError(f"96 MHz PGO changed {name} capture bytes; all artifacts retained")
            verification.append({"name": name, "scenario": scenario, **pair})
    return verification, {"workers": workers, "wall_seconds": time.monotonic() - started}


def current(saved=None):
    try:
        if saved is None:
            saved = json.loads(RECEIPT.read_text())
        return (
            saved.get("passed") is True
            and system96.current()
            and saved["inputs"] == system96.inputs()
            and saved["recipe"] == digest(Path(__file__))
            and saved["sources"] == build.hashes([ROOT / name for name in saved["sources"]])
            and saved["architecture"] == platform.machine()
            and saved["counters"] == build.hashes(DATA.glob("*.gcda"))
            and bool(saved["counters"])
            and saved["binary"] == digest(BINARY)
            and profile.captures_current(DIRECTORY, saved["verification"])
        )
    except (OSError, KeyError, ValueError, TypeError):
        return False


def faster():
    try:
        saved = json.loads(RECEIPT.read_text())
        return current(saved) and all(
            0 < case["optimized"]["cpu_seconds"] < case["baseline"]["cpu_seconds"]
            for case in saved["verification"]
        )
    except (OSError, KeyError, ValueError, TypeError):
        return False


def main():
    boot_cache.configure(clock_multiplier=3)
    if current():
        print("Verified unchanged 96 MHz PGO simulator", flush=True)
        return
    if not system96.current() or not boot_cache.valid("dut"):
        raise ValueError(
            "A current 96 MHz baseline and its independently executed own boot are required"
        )
    before, boot = system96.inputs(), boot_cache.state_hashes("dut")
    source = {
        **profile.capture_sources(),
        **build.hashes([Path(__file__), Path(profile.__file__), system96.BINARY]),
    }
    if DIRECTORY.exists():
        # A previous result is evidence, even if its recipe is now stale.
        raise ValueError("Existing 96 MHz profile is retained; move it before a new training run")
    DATA.mkdir(parents=True)
    NATIVE.mkdir()
    cases = profile.workloads({"equipment": 1000000, "flight-combat": 2000000})
    # Longer equipment and cargo training slowed the qualification captures.
    training = {
        "duration_us": 375000,
        "events": [{"at_us": 100000, "keys": ["F0"]}, {"at_us": 112500, "keys": []}],
        "checkpoints": [{"at_us": 125000, "name": "first"}, {"at_us": 375000, "name": "final"}],
    }
    print("Compiling instrumented actual 96 MHz simulator", flush=True)
    training_fast_clock = compile_profile("generate")
    print("Training on the original status screen and a physical F0 key pulse", flush=True)
    boot_state = boot_cache.STATES[0]
    capture(BINARY, training, "training/status", boot_state)
    print("Compiling 96 MHz profile; missing/mismatched counters are errors", flush=True)
    fast_clock = compile_profile("use")
    if training_fast_clock != fast_clock:
        raise ValueError("Profile training and verification built different clock drivers")
    print("Comparing 96 MHz baseline/PGO on equipment and flight combat", flush=True)
    verification, qualification = qualify(
        {name: cases[name] for name in ("equipment", "flight-combat")}, boot_state
    )
    checks = {
        "unchanged_inputs": before == system96.inputs() and system96.current(),
        "unchanged_own_boot": boot == boot_cache.state_hashes("dut"),
        "unchanged_controller_and_baseline": source
        == build.hashes([ROOT / name for name in source]),
        "exact_captures": all(
            case["baseline"]["hashes"] == case["optimized"]["hashes"] for case in verification
        ),
    }
    result = {
        "passed": all(checks.values()),
        "checks": checks,
        "inputs": before,
        "recipe": digest(Path(__file__)),
        "architecture": platform.machine(),
        "idle_falling_edge": fast_clock,
        "training_boot": boot,
        "training_scenario": training,
        "counters": build.hashes(DATA.glob("*.gcda")),
        "binary": digest(BINARY),
        "verification": verification,
        "qualification": qualification,
        "sources": source,
        "scope": "Compiler optimization of the actual 96 MHz model using the same guarded clock driver as the baseline. Own baseline boot is restored unchanged. A short status-screen replay trains the profile. Separate equipment and flight captures, including their bus traces, must match byte for byte. CPU and queued wall time are recorded separately.",
        "limits": "Finite compiler verification, not full game equivalence or an isolated throughput benchmark. The baseline simulator is not replaced by this tool.",
    }
    write_json(RECEIPT, result)
    if not current():
        raise ValueError("96 MHz profile receipt failed verification")
    print("Verified separate 96 MHz PGO simulator; baseline retained", flush=True)


if __name__ == "__main__":
    main()
