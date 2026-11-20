"""Observe FDC byte counters during search, WRITE preamble and payload."""

import json
import re
import subprocess
from pathlib import Path

from tools.validation.common import ROOT, source_root, write_json

from . import build, disc_commands, disc_restart, probe

CASES = (("read", 15803), ("write", 15807), ("verify", 14779), ("write-edges", 2105))


def validate_cases(metadata):
    cases = metadata.get("cases", [])
    if (
        metadata.get("own_cold_cases") is not True
        or tuple((c.get("name"), c.get("rows")) for c in cases) != CASES
        or any(type(c.get("rows")) is not int for c in cases)
        or any(c.get("fixture") != f"original.json.{c['name']}.tsv" for c in cases)
    ):
        raise ValueError("Incomplete original counter phase histories")
    return cases


def complete(result, rows):
    return (
        type(rows) is int
        and rows in {n for _, n in CASES}
        and result.returncode == 0
        and not result.stderr
        and result.stdout == f"{rows} original bus observations; 0 differences\n"
    )


def wrong_sync_count(text):
    for signal, operator in (("byteCounterLow", "<="), ("eventCounterLow", "=")):
        text, count = re.subn(rf"\b{signal} {operator} 8'h05;", f"{signal} {operator} 8'h80;", text)
        if count != 1:
            raise ValueError("Missing actual WRITE sync counter fault target: " + signal)
    return text


def qualify():
    directory = build.BBC / "disc-counter-phases"
    driver = ROOT / "sim/native/bbc_disc_test.cpp"
    reference = ROOT / "tools/bbc/reference_disc_counter_phases.mjs"
    sources = [
        Path(__file__),
        Path(disc_commands.__file__),
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
        disc_commands.validate_fixture(fixture, case["rows"], 0)
        writes = directory / (case["name"] + ".writes.tsv")
        run = subprocess.run(
            [str(directory / "VIntel8271"), str(fixture), str(build.BBC / "disc.ssd"), str(writes)],
            cwd=build.BBC,
            capture_output=True,
            text=True,
            timeout=300,
        )
        log = directory / (case["name"] + ".log")
        log.write_text(run.stdout + run.stderr)
        results.append(
            {
                "name": case["name"],
                "passed": complete(run, case["rows"])
                and disc_commands.expected_payload(
                    writes, "write" if case["name"] == "write-edges" else case["name"]
                ),
            }
        )
        artifacts.extend([fixture, writes, log])
        print(f"FDC counter phases {case['name']}: {results[-1]['passed']}", flush=True)
    negative = {"name": "wrong-write-sync-count", "detected": False, "attempted": False}
    if all(r["passed"] for r in results):
        fault_directory = directory / "wrong-sync-count"
        binary = disc_restart.compile_fault(
            fault_directory,
            wrong_sync_count((directory / "devices.v").read_text()),
            driver,
            "WRITE sync count changed from five to 128 only",
        )
        writes = fault_directory / "writes.tsv"
        run = subprocess.run(
            [
                str(binary),
                str(directory / cases[-1]["fixture"]),
                str(build.BBC / "disc.ssd"),
                str(writes),
            ],
            cwd=build.BBC,
            capture_output=True,
            text=True,
            timeout=300,
        )
        log = fault_directory / "result.log"
        log.write_text(run.stdout + run.stderr)
        negative.update(
            attempted=True,
            detected=run.returncode == 1
            and re.fullmatch(r"2105 original bus observations; [1-9]\d* differences\n", run.stdout)
            is not None,
        )
        artifacts.extend(
            [writes, log, fault_directory / "devices.v", fault_directory / "build.json"]
        )
    stable = (
        before == build.hashes([*sources, build.BBC / "disc.ssd"])
        and build.generation_current()
        and probe.device_rtl(["Intel8271"]) == (directory / "devices.v").read_text()
        and inputs["sources"]
        == build.hashes([directory / "devices.v", Path(probe.__file__), *sources])
    )
    receipt = {
        "passed": stable and all(r["passed"] for r in results) and negative["detected"],
        "inputs": inputs,
        "sources": before,
        "inputs_unchanged": stable,
        "results": results,
        "rows": sum(c["rows"] for c in cases),
        "negative_control": negative,
        "artifacts": build.hashes(artifacts),
        "scope": "Four own cold original public host histories. Counter reads through search, WRITE gap/sync, payload and completion, including closely spaced reads at WRITE preamble boundaries. All physical 32 MHz edges execute; complete actual stored payloads and their deadlines checked. No original state enters the DUT.",
        "limits": "Finite standard SSD histories, not all internal registers, arbitrary counter writes, DMA/media faults, formatting or universal game equivalence.",
        "universal_game_equivalence": False,
    }
    write_json(directory / "result.json", receipt)
    if not receipt["passed"]:
        raise ValueError("FDC counter phases differ: " + str(directory))
    return receipt


if __name__ == "__main__":
    qualify()
