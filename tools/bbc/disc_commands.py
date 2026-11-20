"""Compare READ ID, VERIFY and BBC DMA rejection at their original deadlines."""

import json
import re
import subprocess
from pathlib import Path

from tools.validation.common import ROOT, source_root, write_json

from . import build, disc_restart, probe

CASES = {
    "commands": (
        ("verify-one", 138, 1),
        ("verify-three", 138, 1),
        ("scan", 140, 1),
        ("scan-deleted", 140, 1),
        ("dma-read", 138, 1),
        ("dma-write", 138, 1),
        ("dma-id", 138, 1),
    ),
    "id": (
        ("index-1", 147, 5),
        ("index-3", 171, 13),
        ("index-0", 519, 129),
        ("skip-index-3", 171, 13),
    ),
    "search": (
        ("sector-ten", 132, 1),
        ("sector-sixteen", 132, 1),
        ("sector-255", 132, 1),
        ("empty-track", 132, 1),
        ("past-last-sector", 900, 257),
        ("mapped-track", 132, 1),
        ("upper-side", 132, 1),
    ),
    "counters": (("retention", 122, 0), ("read", 1171, 0), ("write", 1171, 0), ("verify", 147, 0)),
}


def validate_cases(metadata, family):
    cases = metadata.get("cases", [])
    if (
        metadata.get("own_cold_cases") is not True
        or tuple((c.get("name"), c.get("rows")) for c in cases)
        != tuple((name, rows) for name, rows, _ in CASES[family])
        or any(type(c.get("rows")) is not int for c in cases)
        or any(c.get("fixture") != f"original-{family}.json.{c['name']}.tsv" for c in cases)
    ):
        raise ValueError("Incomplete original command histories")
    return cases


def validate_fixture(path, rows, passive):
    lines = [line.split() for line in path.read_text().splitlines()]
    if len(lines) != rows or any(len(line) != 5 for line in lines):
        raise ValueError("Incomplete original command observations")
    probes = [line for line in lines if line[1] == "P"]
    if len(probes) != passive or any(line[2:] != ["0", "128", "0"] for line in probes):
        raise ValueError("Missing passive pre-interrupt deadlines")


def complete(result, rows):
    return (
        type(rows) is int
        and rows in {row for family in CASES.values() for _, row, _ in family}
        and result.returncode == 0
        and not result.stderr
        and result.stdout == f"{rows} original bus observations; 0 differences\n"
    )


def disabled_verify(text):
    target = "(command != 8'h1c)"
    if text.count(target) != 1:
        raise ValueError("Missing actual VERIFY negative-control target")
    return text.replace(target, "1'b1")


def disabled_search_timeout(text):
    # Expiry gates the final serial cell as well as completing the search.
    # Cut both projections of this timeout, keeping unrelated index work live.
    anchors = (
        r"(?m)^  assign indexExpires = \(\(\(\(preIndexCallback == 2'b01\) && preIndexSpinning\) && index\) && \(preIndexCount == 8'h01\)\);$",
        r"(?m)^  assign when_Intel8271_l\d+ = \(\(\(preIndexCallback == 2'b01\) && preIndexSpinning\) && index\);$",
    )
    for anchor in anchors:
        matches = re.findall(anchor, text)
        if len(matches) != 1:
            raise ValueError("Missing unique actual search timeout negative-control target")
        statement = matches[0]
        text = text.replace(statement, statement.split(" = ", 1)[0] + " = 1'b0;", 1)
    return text


def disconnected_counter(text):
    broken, count = re.subn(r"io_result = eventCounterHigh;", "io_result = 8'hff;", text)
    if count != 1:
        raise ValueError("Missing actual byte-counter negative-control target")
    return broken


def expected_payload(path, name):
    if name != "write":
        return path.stat().st_size == 0
    rows = [list(map(int, line.split())) for line in path.read_text().splitlines()]
    return (
        len(rows) == 256
        and all(len(row) == 3 for row in rows)
        and [[address, value] for _, address, value in rows]
        == [[8192 + i, (19 * i + 3) & 255] for i in range(256)]
        and all(b[0] - a[0] == 128 for a, b in zip(rows, rows[1:]))
    )


def qualify():
    directory = build.BBC / "disc-commands"
    driver = ROOT / "sim/native/bbc_disc_test.cpp"
    references = {family: ROOT / f"tools/bbc/reference_disc_{family}.mjs" for family in CASES}
    sources = [
        Path(__file__),
        Path(disc_restart.__file__),
        driver,
        *references.values(),
        ROOT / "tools/bbc/reference_drive.mjs",
    ]
    inputs = probe.compile_probe(directory, "Intel8271", sources, ["Intel8271"])
    before = build.hashes([*sources, build.BBC / "disc.ssd"])
    artifacts, results, cases = [], [], []
    for family, reference in references.items():
        original = directory / f"original-{family}.json"
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
        selected = validate_cases(json.loads(original.read_text()), family)
        cases.extend(selected)
        artifacts.append(original)
        for case, (_, _, passive) in zip(selected, CASES[family], strict=True):
            fixture = directory / case["fixture"]
            validate_fixture(fixture, case["rows"], passive)
            writes = directory / (case["name"] + ".writes.tsv")
            run = subprocess.run(
                [
                    str(directory / "VIntel8271"),
                    str(fixture),
                    str(build.BBC / "disc.ssd"),
                    str(writes),
                ],
                cwd=build.BBC,
                capture_output=True,
                text=True,
                timeout=180,
            )
            log = directory / (case["name"] + ".log")
            log.write_text(run.stdout + run.stderr)
            results.append(
                {
                    "name": case["name"],
                    "passed": complete(run, case["rows"])
                    and expected_payload(writes, case["name"]),
                }
            )
            artifacts.extend([fixture, writes, log])
            print(f"FDC command {case['name']}: {results[-1]['passed']}", flush=True)
    fault_directory = directory / "disabled-verify"
    fault = disc_restart.compile_fault(
        fault_directory,
        disabled_verify((directory / "devices.v").read_text()),
        driver,
        "live VERIFY marker IRQ suppression disabled only",
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
        and re.fullmatch(r"138 original bus observations; [1-9]\d* differences\n", negative.stdout)
        is not None
    )
    artifacts.extend(
        [fault_writes, fault_log, fault_directory / "devices.v", fault_directory / "build.json"]
    )
    timeout_directory = directory / "disabled-timeout"
    timeout_fault = disc_restart.compile_fault(
        timeout_directory,
        disabled_search_timeout((directory / "devices.v").read_text()),
        driver,
        "three-index search timeout disabled only",
    )
    search = next(case for case in cases if case["name"] == "sector-ten")
    timeout_writes = timeout_directory / "writes.tsv"
    timeout_run = subprocess.run(
        [
            str(timeout_fault),
            str(directory / search["fixture"]),
            str(build.BBC / "disc.ssd"),
            str(timeout_writes),
        ],
        cwd=build.BBC,
        capture_output=True,
        text=True,
        timeout=180,
    )
    timeout_log = timeout_directory / "result.log"
    timeout_log.write_text(timeout_run.stdout + timeout_run.stderr)
    timeout_detected = (
        timeout_run.returncode == 1
        and re.fullmatch(
            r"132 original bus observations; [1-9]\d* differences\n", timeout_run.stdout
        )
        is not None
    )
    artifacts.extend(
        [
            timeout_writes,
            timeout_log,
            timeout_directory / "devices.v",
            timeout_directory / "build.json",
        ]
    )
    counter_directory = directory / "disconnected-counter"
    counter_fault = disc_restart.compile_fault(
        counter_directory,
        disconnected_counter((directory / "devices.v").read_text()),
        driver,
        "host counter-high readback disconnected only",
    )
    retained = next(case for case in cases if case["name"] == "retention")
    counter_writes = counter_directory / "writes.tsv"
    counter_run = subprocess.run(
        [
            str(counter_fault),
            str(directory / retained["fixture"]),
            str(build.BBC / "disc.ssd"),
            str(counter_writes),
        ],
        cwd=build.BBC,
        capture_output=True,
        text=True,
        timeout=180,
    )
    counter_log = counter_directory / "result.log"
    counter_log.write_text(counter_run.stdout + counter_run.stderr)
    counter_detected = (
        counter_run.returncode == 1
        and re.fullmatch(
            r"122 original bus observations; [1-9]\d* differences\n", counter_run.stdout
        )
        is not None
    )
    artifacts.extend(
        [
            counter_writes,
            counter_log,
            counter_directory / "devices.v",
            counter_directory / "build.json",
        ]
    )
    stable = (
        before == build.hashes([*sources, build.BBC / "disc.ssd"]) and build.generation_current()
    )
    receipt = {
        "passed": stable
        and detected
        and timeout_detected
        and counter_detected
        and all(case["passed"] for case in results),
        "inputs": inputs,
        "sources": before,
        "inputs_unchanged": stable,
        "cases": cases,
        "results": results,
        "rows": sum(c["rows"] for c in cases),
        "negative_controls": [
            {"name": "disabled-verify", "detected": detected},
            {"name": "disabled-timeout", "detected": timeout_detected},
            {"name": "disconnected-counter", "detected": counter_detected},
        ],
        "artifacts": build.hashes(artifacts),
        "scope": "Twenty-two own cold public original histories and actual generated controller at every 32 MHz physical edge. READ ID index wait/bypass, header order, repeated headers and zero count meaning 32; VERIFY one/three sectors; BBC scan and read/write/ID DMA rejection; missing-sector searches, sector-number alias prevention, empty track/upper side, last-sector overrun and bad-track retries. Command/ID/search histories include passive busy/NMI observations on the original slot immediately before every byte/completion, preventing early held interrupts from passing. Counter histories observe host aliases 2/3 at every read/write request, retain counters across host reset, and read live SPECIAL registers 00/13/14. Only the WRITE history may store its 256 precisely addressed payload bytes.",
        "limits": "Finite standard-SSD histories; complete NMI waveforms, malformed headers/CRC, formatting and arbitrary commands are not proven.",
        "universal_game_equivalence": False,
    }
    write_json(directory / "result.json", receipt)
    if not receipt["passed"]:
        raise ValueError("FDC command deadlines differ; all histories retained")
    return receipt


if __name__ == "__main__":
    qualify()
