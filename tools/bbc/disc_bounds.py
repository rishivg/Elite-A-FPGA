"""Compare writes and readback at the end of the declared 80-track medium."""

import json
import re
import subprocess
from pathlib import Path

from tools.validation.common import ROOT, source_root, write_json

from . import build, disc_restart, probe


def validate_cases(metadata):
    cases = metadata.get("cases", [])
    expected = [
        {
            "track": track,
            "sector": 9,
            "written": 256,
            "readback": 256,
            "rows": 1061,
            "fixture": f"original.json.track{track}.tsv",
        }
        for track in (76, 79)
    ]
    if metadata.get("own_cold_cases") is not True or cases != expected:
        raise ValueError("Incomplete original last-sector histories")
    # bools compare equal to integers in Python; receipts require actual counts.
    if any(type(case[key]) is not int for case in cases for key in expected[0] if key != "fixture"):
        raise ValueError("Malformed original last-sector counts")
    return cases


def complete(result, rows):
    return (
        type(rows) is int
        and rows == 1061
        and result.returncode == 0
        and not result.stderr
        and result.stdout == "1061 original bus observations; 0 differences\n"
    )


def payload_matches(path, track):
    rows = [list(map(int, line.split())) for line in path.read_text().splitlines()]
    start = track * 2560 + 9 * 256
    return (
        len(rows) == 256
        and all(len(row) == 3 for row in rows)
        and [[address, value] for _, address, value in rows]
        == [[start + i, (i * 19 + 3) & 255] for i in range(256)]
        and all(b[0] - a[0] == 128 for a, b in zip(rows, rows[1:]))
    )


def unbounded_address(text):
    """Reconnect the actual pre-clamp address; leave all active transfer logic intact."""
    broken, count = re.subn(
        r"(?m)^  assign io_storageAddress = .*;$",
        "  assign io_storageAddress = requestedStorageAddress;",
        text,
    )
    if count != 1 or not re.search(r"wire\s+\[17:0\]\s+requestedStorageAddress;", text):
        raise ValueError("Missing actual medium address bound negative-control target")
    return broken


def qualify():
    directory = build.BBC / "disc-bounds"
    driver = ROOT / "sim/native/bbc_disc_test.cpp"
    reference = ROOT / "tools/bbc/reference_disc_bounds.mjs"
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
        writes = directory / f"track{case['track']}.writes.tsv"
        run = subprocess.run(
            [str(directory / "VIntel8271"), str(fixture), str(build.BBC / "disc.ssd"), str(writes)],
            cwd=build.BBC,
            capture_output=True,
            text=True,
            timeout=180,
        )
        log = directory / f"track{case['track']}.log"
        log.write_text(run.stdout + run.stderr)
        matched = complete(run, case["rows"]) and payload_matches(writes, case["track"])
        results.append({"track": case["track"], "passed": matched})
        artifacts.extend([fixture, writes, log])
        print(f"FDC last sector track {case['track']}: {matched}", flush=True)
    fault_directory = directory / "unbounded-address"
    fault = disc_restart.compile_fault(
        fault_directory,
        unbounded_address((directory / "devices.v").read_text()),
        driver,
        "external storage address bound removed only",
    )
    fault_writes = fault_directory / "writes.tsv"
    negative = subprocess.run(
        [
            str(fault),
            str(directory / cases[-1]["fixture"]),
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
        negative.returncode == 2
        and "204800" in negative.stderr
        and "_M_range_check" in negative.stderr
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
        "negative_control": {"name": "unbounded-address", "detected": detected},
        "artifacts": build.hashes(artifacts),
        "scope": "Own cold original and actual generated controller. Public write/readback at sector 9 of tracks 76 and 79, every 32 MHz physical edge. All 256 stored payload bytes and their 64 us spacing; storage addresses checked at every edge by the native backend.",
        "limits": "Declared standard 80-track SSD only. Invalid commands, malformed flux, CRC corruption, formatting and arbitrary media are not proven.",
        "universal_game_equivalence": False,
    }
    write_json(directory / "result.json", receipt)
    if not receipt["passed"]:
        raise ValueError("FDC medium bounds differ; complete histories retained")
    return receipt


if __name__ == "__main__":
    qualify()
