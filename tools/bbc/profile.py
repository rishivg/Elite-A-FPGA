"""Train and verify compiler PGO using original Elite-A and physical keys."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import subprocess
import time
from pathlib import Path

from tools.validation.common import HERE, ROOT, digest, write_json
from tools.validation.run import compile_scenario
from tools.validation.setup import verify

from . import boot_cache, build
from .capture import native_environment, scenario_command
from .capture import run as run_capture
from .inputs import write_schedule

DIRECTORY = build.BBC / "profile"
NATIVE = DIRECTORY / "native"
DATA = DIRECTORY / "counters"
RECEIPT = DIRECTORY / "receipt.json"


def artifacts(directory):
    return {p.name: digest(p) for p in sorted(directory.iterdir()) if p.is_file()}


def target_info():
    """Bind host tuning to the compiler's actual instruction-set macros."""
    architecture = platform.machine()
    flags = (
        ["-march=native"]
        if architecture in ["x86_64", "AMD64"]
        else ["-mcpu=native"]
        if architecture in ["aarch64", "arm64"]
        else []
    )
    macros = subprocess.check_output(["c++", *flags, "-dM", "-E", "-x", "c++", "-"], input=b"")
    return {
        "architecture": architecture,
        "flags": flags,
        "compiler_target_sha256": hashlib.sha256(macros).hexdigest(),
    }


def capture_sources():
    return build.hashes(
        [
            Path(__file__).with_name("capture.py"),
            Path(__file__).with_name("inputs.py"),
            HERE / "run.py",
            HERE / "scenarios.json",
        ]
    )


def captures_current(directory, verification):
    return len(verification) == 2 and all(
        case["baseline"]["hashes"]
        == case["optimized"]["hashes"]
        == artifacts(directory / case["name"] / "baseline/capture")
        == artifacts(directory / case["name"] / "optimized/capture")
        for case in verification
    )


def current(receipt):
    """No profile reuse after a source, compiler, counter or capture mutation."""
    try:
        saved = json.loads(RECEIPT.read_text())
        return (
            receipt == saved
            and saved["schema"] == 2
            and saved["target"] == target_info()
            and saved["inputs"] == build.native_inputs()
            and saved["recipe"] == digest(Path(__file__))
            and saved["capture_sources"] == capture_sources()
            and saved["counters"] == build.hashes(DATA.glob("*.gcda"))
            and bool(saved["counters"])
            and saved["binary"] == digest(NATIVE / "VBbcMachine")
            and captures_current(DIRECTORY, saved["verification"])
        )
    except (OSError, ValueError, KeyError, TypeError):
        return False


def command(mode):
    flags = f"-flto -fprofile-{mode}={DATA} " + " ".join(target_info()["flags"])
    if mode == "use":
        flags += " -Werror=coverage-mismatch -Werror=missing-profile"
    return [
        "verilator",
        "--cc",
        "--exe",
        "--build",
        "-j",
        os.environ.get("VERILATOR_JOBS", "4"),
        "--savable",
        "-Wno-fatal",
        "--top-module",
        "BbcMachine",
        "-CFLAGS",
        "-O3 -std=c++17 " + flags,
        "-LDFLAGS",
        flags + " -lcrypto",
        "-MAKEFLAGS",
        "-B OPT_FAST=-O3",
        "--Mdir",
        str(NATIVE),
        "build/bbc/BbcMachine.v",
        "rtl/bbc/BbcCpu.v",
        "build/bbc/ArletCpuEnabled.v",
        "third_party/6502/ALU.v",
        str(ROOT / "sim/native/bbc.cpp"),
    ]


def execute(command, cwd, log, env=None, timeout=600):
    with log.open("w") as output:
        subprocess.run(
            command,
            cwd=cwd,
            env=env,
            stdout=output,
            stderr=subprocess.STDOUT,
            check=True,
            timeout=timeout,
        )


def capture(binary, scenario, name):
    directory = DIRECTORY / name
    if directory.exists():
        shutil.rmtree(directory)
    directory.mkdir(parents=True)
    write_schedule(directory, scenario)
    env = native_environment(load=build.BBC / "boot.state")
    started = time.monotonic()
    run_capture(
        scenario_command(binary, directory, scenario, "capture"),
        build.BBC,
        directory / "capture.log",
        env,
    )
    return {"seconds": time.monotonic() - started, "hashes": artifacts(directory / "capture")}


def workloads(durations):
    """Compile real key sequences and keep checkpoints inside each requested prefix."""
    contract = json.loads((HERE / "scenarios.json").read_text())
    cases = {s["name"]: compile_scenario(s, contract["actions"]) for s in contract["scenarios"]}
    for name, duration in durations.items():
        case = cases[name]
        case["duration_us"] = duration
        for key in ("events", "checkpoints"):
            case[key] = [row for row in case[key] if row["at_us"] < duration]
    return cases


def main():
    verify()
    if not build.native_current():
        raise ValueError("Run make bbc-build before profiling")
    receipt = build.load_receipt("native-build.json")
    if receipt.get("optimization") and current(receipt["optimization"]):
        print(
            "Verified unchanged profile, compilation and identical captures; reusing PGO simulator"
        )
        return
    DIRECTORY.mkdir(parents=True, exist_ok=True)
    before = build.native_inputs()
    if not boot_cache.valid("dut"):
        print("Booting original ROM/disc independently before profile training", flush=True)
        boot_inputs = boot_cache.inputs("dut")
        run_capture(
            [
                str(build.BBC / "native/VBbcMachine"),
                str(boot_cache.BOOT_CYCLES),
                str(build.BBC / "boot.keys"),
                "-",
                str(DIRECTORY / "boot-dut"),
            ],
            build.BBC,
            DIRECTORY / "boot.log",
            native_environment(save=build.BBC / "boot.state"),
            timeout=boot_cache.BOOT_TIMEOUT_SECONDS,
        )
        boot_cache.seal(DIRECTORY, boot_inputs, "dut")
    boot_hashes = boot_cache.state_hashes("dut")
    sources = capture_sources()
    recipe = digest(Path(__file__))
    cases = workloads({"equipment": 1000000, "flight-combat": 6000000})
    equipment, flight = cases["equipment"], cases["flight-combat"]
    if DATA.exists():
        shutil.rmtree(DATA)
    DATA.mkdir()
    NATIVE.mkdir(exist_ok=True)
    print("Compiling instrumented simulator", flush=True)
    execute(command("generate"), ROOT, DIRECTORY / "generate.log")
    for case in [equipment, cases["buy-cargo"]]:
        print("Training on " + case["name"], flush=True)
        capture(NATIVE / "VBbcMachine", case, "training/" + case["name"])
    print("Compiling profile with missing/mismatched counters treated as errors", flush=True)
    execute(command("use"), ROOT, DIRECTORY / "compile.log")
    verification = []
    for case in [equipment, flight]:
        print("Comparing baseline/PGO captures: " + case["name"], flush=True)
        baseline = capture(build.BBC / "native/VBbcMachine", case, case["name"] + "/baseline")
        optimized = capture(NATIVE / "VBbcMachine", case, case["name"] + "/optimized")
        if baseline["hashes"] != optimized["hashes"]:
            raise ValueError("Compiler optimization changed guest artifacts: " + case["name"])
        verification.append(
            {"name": case["name"], "scenario": case, "baseline": baseline, "optimized": optimized}
        )
    if (
        before != build.native_inputs()
        or boot_hashes != boot_cache.state_hashes("dut")
        or sources != capture_sources()
        or recipe != digest(Path(__file__))
    ):
        raise ValueError("Source, capture recipe or training boot changed during profiling")
    optimized = {
        "schema": 2,
        "target": target_info(),
        "inputs": before,
        "recipe": recipe,
        "capture_sources": sources,
        "training_boot": boot_hashes,
        "counters": build.hashes(DATA.glob("*.gcda")),
        "binary": digest(NATIVE / "VBbcMachine"),
        "verification": verification,
    }
    write_json(RECEIPT, optimized)
    if not current(optimized):
        raise ValueError("Profile receipt failed verification")
    shutil.copy2(NATIVE / "VBbcMachine", build.BBC / "native/VBbcMachine")
    write_json(
        build.BBC / "native-build.json",
        {"inputs": before, "binary": optimized["binary"], "optimization": optimized},
    )
    print(
        "Activated verified PGO simulator; unchanged independently executed own boot remains valid",
        flush=True,
    )


if __name__ == "__main__":
    main()
