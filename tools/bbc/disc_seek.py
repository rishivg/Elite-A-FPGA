"""Compare public seek behavior, mechanical registers and motor-stop recovery."""

import json
import re
import subprocess
from pathlib import Path

from tools.validation.common import ROOT, source_root, write_json

from . import build, disc_restart, probe

CASES = (
    ("direction-current", [1, 42, 43, 79, 42, 0], [255, 255], 0xC1, 203, False),
    ("bad-tracks", [1, 2, 3, 4, 0], [2, 4], 0xC1, 190, False),
    ("bad-track-wrap", [254, 255, 0], [254, 255], 0xC1, 164, False),
    ("single-actuator", [2, 5, 0], [255, 255], 0xC3, 164, False),
    ("unloaded-head", [4, 0, 6], [255, 255], 0xC1, 1727, True),
)


def validate_cases(metadata):
    cases = metadata.get("cases", [])
    actual = tuple(
        (
            c.get("name"),
            c.get("tracks"),
            c.get("bad"),
            c.get("mode"),
            c.get("rows"),
            c.get("unload", False),
        )
        for c in cases
    )
    if (
        metadata.get("own_cold_cases") is not True
        or actual != CASES
        or any(type(c.get("rows")) is not int for c in cases)
        or any(c.get("fixture") != f"original.json.{c['name']}.tsv" for c in cases)
        or any([o.get("track") for o in c.get("observations", [])] != c["tracks"] for c in cases)
    ):
        raise ValueError("Incomplete original mechanical seek histories")
    return cases


def complete(result, rows):
    return (
        type(rows) is int
        and rows in {case[4] for case in CASES}
        and result.returncode == 0
        and not result.stderr
        and result.stdout == f"{rows} original bus observations; 0 differences\n"
    )


def held_head_current(text):
    broken, count = re.subn(r"driveOut\[4\] <= [^;]+;", "driveOut[4] <= 1'b0;", text)
    if count != 1:
        raise ValueError("Missing actual head-current negative-control target")
    return broken


def qualify():
    directory = build.BBC / "disc-seek"
    driver = ROOT / "sim/native/bbc_disc_test.cpp"
    reference = ROOT / "tools/bbc/reference_disc_seek.mjs"
    sources = [
        Path(__file__),
        Path(disc_restart.__file__),
        driver,
        reference,
        ROOT / "tools/bbc/reference_drive.mjs",
    ]
    inputs = probe.compile_probe(directory, "Intel8271", sources, ["Intel8271"])
    before = build.hashes([*sources, build.BBC / "disc.ssd"])
    original = directory / "original.json"
    subprocess.run(
        [
            "node",
            str(reference),
            str(source_root("jsbeeb")),
            str(build.BBC / "disc.ssd"),
            str(original),
        ],
        cwd=ROOT,
        check=True,
        timeout=60,
    )
    cases = validate_cases(json.loads(original.read_text()))
    artifacts, results = [original], []
    for case in cases:
        fixture = directory / case["fixture"]
        writes = directory / (case["name"] + ".writes.tsv")
        run = subprocess.run(
            [str(directory / "VIntel8271"), str(fixture), str(build.BBC / "disc.ssd"), str(writes)],
            cwd=build.BBC,
            capture_output=True,
            text=True,
            timeout=180,
        )
        log = directory / (case["name"] + ".log")
        log.write_text(run.stdout + run.stderr)
        results.append({"name": case["name"], "passed": complete(run, case["rows"])})
        artifacts.extend([fixture, writes, log])
        print(f"FDC mechanical seek {case['name']}: {results[-1]['passed']}", flush=True)
    fault_directory = directory / "held-head-current"
    fault = disc_restart.compile_fault(
        fault_directory,
        held_head_current((directory / "devices.v").read_text()),
        driver,
        "head-current output forced low only",
    )
    fault_writes = fault_directory / "writes.tsv"
    negative = subprocess.run(
        [
            str(fault),
            str(directory / cases[0]["fixture"]),
            str(build.BBC / "disc.ssd"),
            str(fault_writes),
        ],
        cwd=build.BBC,
        capture_output=True,
        text=True,
        timeout=180,
    )
    fault_log = fault_directory / "result.log"
    fault_log.write_text(negative.stdout + negative.stderr)
    detected = (
        negative.returncode == 1
        and re.fullmatch(r"203 original bus observations; [1-9]\d* differences\n", negative.stdout)
        is not None
    )
    artifacts.extend(
        [fault_writes, fault_log, fault_directory / "devices.v", fault_directory / "build.json"]
    )
    stable = (
        before == build.hashes([*sources, build.BBC / "disc.ssd"]) and build.generation_current()
    )
    receipt = {
        "passed": stable and detected and all(case["passed"] for case in results),
        "inputs": inputs,
        "sources": before,
        "inputs_unchanged": stable,
        "cases": cases,
        "results": results,
        "rows": sum(c["rows"] for c in cases),
        "negative_control": {"name": "held-head-current", "detected": detected},
        "artifacts": build.hashes(artifacts),
        "scope": "Five own cold public original histories and actual generated controller at every 32 MHz physical edge. Seek direction and track-43 head-current boundary; ordered bad-track translation and byte wrapping; shared logical track latches in single-actuator mode; stop motor, seek, load head and read full sectors on tracks 4, 0 and 6.",
        "limits": "Finite declared standard-SSD histories. Raw flux, formatting, READ ID, every dynamic internal register and arbitrary commands remain unproven.",
        "universal_game_equivalence": False,
    }
    write_json(directory / "result.json", receipt)
    if not receipt["passed"]:
        raise ValueError("FDC mechanical seeks differ; complete histories retained")
    return receipt


if __name__ == "__main__":
    qualify()
