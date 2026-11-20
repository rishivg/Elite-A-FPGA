"""Compare interrupted-seek recovery through independently executed public host commands."""

import json
import re
import subprocess
from pathlib import Path

from tools.validation.common import ROOT, source_root, write_json

from . import build, disc_restart, probe

CASES = (
    tuple(
        (0x40, target, elapsed, target)
        for target in (2, 5, 10)
        for elapsed in (20, 47950, 48000, 48050, 100000, 200000)
    )
    + tuple((0x40, target, elapsed, 0) for target in (2, 5, 10) for elapsed in (20, 100000))
    + ((0x80, 2, 20, 0), (0x80, 5, 100000, 0), (0x80, 1, 20, 1))
)


def case_name(select, target, elapsed, read_track):
    return (
        ("drive1-" if select == 0x80 else "")
        + f"target{target}-abort{elapsed}"
        + ("-home" if read_track == 0 else "")
    )


def validate_cases(metadata):
    cases = metadata.get("cases", [])
    if (
        tuple(
            (c.get("select"), c.get("target"), c.get("elapsed"), c.get("read_track")) for c in cases
        )
        != CASES
        or metadata.get("own_cold_cases") is not True
        or any(type(c.get("rows")) is not int or c["rows"] != 145 for c in cases)
        or metadata.get("rows") != sum(c["rows"] for c in cases)
        or any(
            c.get("name") != case_name(c["select"], c["target"], c["elapsed"], c["read_track"])
            or c.get("fixture") != f"original.json.{c['name']}.tsv"
            for c in cases
        )
    ):
        raise ValueError("Incomplete original seek-recovery fixture")
    return cases


def complete(result, rows):
    return (
        type(rows) is int
        and rows == 145
        and result.returncode == 0
        and not result.stderr
        and result.stdout == f"{rows} original bus observations; 0 differences\n"
    )


def held_head(text):
    """Hold drive 0's head at its reset position; leave logical seeks active."""
    broken, count = re.subn(
        r"physicalTrack <= \(physicalTrack [+-] 8'h01\);",
        "physicalTrack <= physicalTrack;",
        text,
    )
    if count != 8:
        raise ValueError("Missing actual physical-head step negative-control target")
    return broken


def qualify():
    directory = build.BBC / "disc-recovery"
    driver = ROOT / "sim/native/bbc_disc_test.cpp"
    reference = ROOT / "tools/bbc/reference_disc_recovery.mjs"
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
    results, artifacts = [], [original]
    for case in cases:
        fixture = directory / case["fixture"]
        writes = directory / (case["name"] + ".writes.tsv")
        log = directory / (case["name"] + ".log")
        result = subprocess.run(
            [str(directory / "VIntel8271"), str(fixture), str(build.BBC / "disc.ssd"), str(writes)],
            cwd=build.BBC,
            capture_output=True,
            text=True,
            timeout=180,
        )
        log.write_text(result.stdout + result.stderr)
        results.append({"name": case["name"], "passed": complete(result, case["rows"])})
        artifacts.extend([fixture, writes, log])
        print(f"FDC seek recovery {case['name']}: {results[-1]['passed']}", flush=True)
    fault_directory = directory / "held-head"
    fault = disc_restart.compile_fault(
        fault_directory,
        held_head((directory / "devices.v").read_text()),
        driver,
        "drive-0 physical head steps disabled only",
    )
    case = cases[0]
    writes = fault_directory / "writes.tsv"
    negative = subprocess.run(
        [str(fault), str(directory / case["fixture"]), str(build.BBC / "disc.ssd"), str(writes)],
        cwd=build.BBC,
        capture_output=True,
        text=True,
        timeout=180,
    )
    log = fault_directory / "result.log"
    log.write_text(negative.stdout + negative.stderr)
    detected = (
        negative.returncode == 1
        and re.fullmatch(r"145 original bus observations; [1-9]\d* differences\n", negative.stdout)
        is not None
    )
    artifacts.extend([writes, log, fault_directory / "devices.v", fault_directory / "build.json"])
    stable = (
        before == build.hashes([*sources, build.BBC / "disc.ssd"]) and build.generation_current()
    )
    receipt = {
        "passed": stable and detected and all(c["passed"] for c in results),
        "inputs": inputs,
        "sources": before,
        "inputs_unchanged": stable,
        "cases": cases,
        "results": results,
        "rows": sum(c["rows"] for c in cases),
        "negative_control": {"name": "held-head", "detected": detected},
        "artifacts": build.hashes(artifacts),
        "scope": "Every 32 MHz physical edge against the original's own cold execution. Public SPECIFY, READY priming, seek, register-2 abort, reselect and sector read. Three targets and six abort timings around a 24 ms step boundary, including partial and completed seeks, six return-to-track-zero recovery histories and three absent-drive-1 recovery histories; accepted status, data/result and NMI observations.",
        "limits": "Finite standard-SSD histories. Arbitrary media, raw flux/CRC errors, formatting, scan and all possible seek/reset timings remain unproven.",
        "universal_game_equivalence": False,
    }
    write_json(directory / "result.json", receipt)
    if not receipt["passed"]:
        raise ValueError("FDC seek recovery differs; complete histories retained")
    return receipt


if __name__ == "__main__":
    qualify()
