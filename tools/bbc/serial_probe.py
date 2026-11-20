"""Check closed-fixture MC6850/serial ULA registers and nominal transmit timing."""

import json
import re
import subprocess
import time
from pathlib import Path

from tools.validation.common import ROOT, digest, source_root, write_json
from tools.validation.setup import verify

from . import build, bus_decode, serial_idle_proof


def qualify(*, all_edges=False):
    verify()
    generation = bus_decode.elaborate()
    idle = None if all_edges else serial_idle_proof.qualify()
    directory = build.BBC / ("serial-probe" if all_edges else "serial-probe-fast")
    directory.mkdir(exist_ok=True)
    rtl = bus_decode.DIRECTORY / "BbcSerial.v"
    helper = ROOT / "tools/bbc/reference_serial.mjs"
    driver = ROOT / "sim/native/bbc_serial_test.cpp"
    dependencies = [
        Path(__file__),
        ROOT / "tools/bbc/bus_decode.py",
        rtl,
        helper,
        driver,
        bus_decode.DIRECTORY / "elaboration.json",
        *sorted((source_root("jsbeeb") / "src").rglob("*.js")),
    ]
    if idle is not None:
        dependencies += [
            build.BBC / "serial-idle-proof/result.json",
            *[ROOT / name for category in ("inputs", "artifacts") for name in idle[category]],
        ]
    before = build.hashes(dependencies)
    fixture = directory / "original.tsv"
    reference = subprocess.run(
        ["node", str(helper), str(source_root("jsbeeb")), str(fixture)],
        check=True,
        capture_output=True,
        text=True,
        timeout=120,
    )
    counts = json.loads(reference.stdout)
    if counts != {
        "cases": 12928,
        "observations": 142720,
        "static_cases": 12288,
        "dynamic_cases": 640,
    }:
        raise ValueError("Incomplete original serial fixture")
    fixture_hash = digest(fixture)
    binary = bus_decode.compile_model(rtl, directory / "actual", top="BbcSerial", driver=driver)
    binary_hash = digest(binary)
    clocks = []
    mode = [] if all_edges else ["--idle-proven"]
    for multiplier in (1, 3):
        started = time.perf_counter()
        result = subprocess.run(
            [str(binary), str(fixture), str(multiplier), *mode],
            capture_output=True,
            text=True,
            timeout=600,
        )
        log = directory / f"clock{multiplier}.log"
        log.write_text(result.stdout + result.stderr)
        passed = (
            result.returncode == 0
            and result.stdout == "BBC serial: 12928 cases; 142720 observations; 0 differences\n"
        )
        clocks.append(
            {
                "clock_multiplier": multiplier,
                "passed": passed,
                "log_sha256": digest(log),
                "seconds": time.perf_counter() - started,
            }
        )
        print(result.stdout + result.stderr, end="", flush=True)
    negatives = []
    for name, target, wrong in (
        (
            "early-completion",
            "remaining <= transmitPeriod;",
            "remaining <= (transmitPeriod - 19'd1);",
        ),
        (
            "wrong-read-selection",
            "assign serialValue = (io_serialRead ? 8'hfe : io_data);",
            "assign serialValue = io_data;",
        ),
    ):
        text = rtl.read_text()
        if text.count(target) != 1:
            raise ValueError("Missing serial negative-control target: " + name)
        model = directory / (name + ".v")
        model.write_text(text.replace(target, wrong))
        bad_binary = bus_decode.compile_model(
            model, directory / name, top="BbcSerial", driver=driver
        )
        # Static cases detect the altered pending deadline immediately, while
        # the same complete fixture still independently checks all timings.
        result = subprocess.run(
            [str(bad_binary), str(fixture), "1", *mode], capture_output=True, text=True, timeout=300
        )
        log = directory / (name + ".log")
        log.write_text(result.stdout + result.stderr)
        match = re.fullmatch(
            r"BBC serial: 12928 cases; 142720 observations; ([1-9]\d*) differences\n", result.stdout
        )
        negatives.append(
            {
                "name": name,
                "detected": result.returncode == 1 and match is not None,
                "differences": int(match[1]) if match else None,
            }
        )
    verify()
    stable = (
        before == build.hashes(dependencies)
        and generation == bus_decode.elaborate()
        and fixture_hash == digest(fixture)
        and binary_hash == digest(binary)
    )
    report = {
        "passed": stable
        and all(clock["passed"] for clock in clocks)
        and all(n["detected"] for n in negatives),
        "production_bound": False,
        "inputs": before,
        "generation": generation,
        "inputs_unchanged": stable,
        "counts": counts,
        "idle_edges_omitted": not all_edges,
        "idle_state_proof": idle,
        "clocks": clocks,
        "negative_controls": negatives,
        "artifacts": build.hashes(
            [fixture, binary]
            + list(directory.glob("*.v"))
            + list(directory.glob("*.log"))
            + list(directory.glob("*/build.json"))
            + list(directory.glob("*/VBbcSerial"))
        ),
        "scope": "Standalone BbcSerial class against unchanged original MC6850, serial ULA and scheduler: all control bytes, eight baud choices, cassette/RS-423 selection, control-reset retention, data/serial reads, pending completion retention across rate/reset changes, transmit restart and deadline collisions. Every enabled endpoint executes; idle edges are omitted only with the sealed arbitrary-state proof, or all physical edges execute in all-edges mode. Internal timer/control observations supplement status and IRQ checks. No CPU state is copied.",
        "limits": "Parent wiring is checked separately by bus_binding_proof; CPU interrupt endpoint validation remains separate. No tape image or attached serial peer is part of this fixture; receive traffic, analogue modulation and physical serial timing are outside this result. This finite component corpus is not full game equivalence.",
    }
    write_json(directory / "result.json", report)
    print(f"BBC serial digital component: {report['passed']}; standalone component", flush=True)
    if not report["passed"]:
        raise ValueError("Original serial comparison failed; see retained logs")
    return report


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--all-edges", action="store_true", help="Execute every physical master edge"
    )
    qualify(all_edges=parser.parse_args().all_edges)
