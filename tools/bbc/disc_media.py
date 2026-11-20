"""Compare writable sector markers, interrupted writes and subsequent recovery."""

import json
import re
import subprocess
from pathlib import Path

from tools.validation.common import ROOT, source_root, write_json

from . import build, disc_batch, disc_commands, disc_restart, probe

CASES = (
    ("normal", 1161, ((256, 0), (256, 0))),
    ("deleted-normal", 649, ((256, 0), (0, 32))),
    ("deleted-both", 1161, ((256, 0), (256, 32))),
    ("deleted-verify", 649, ((256, 0), (0, 32))),
    ("reset-deleted", 660, ((256, 0), (0, 32))),
    ("rewrite-normal", 1679, ((256, 0), (256, 0), (256, 0))),
    ("abort-one", 148, ((1, "aborted"), (0, 8))),
    ("abort-two", 662, ((2, "aborted"), (256, 14))),
    ("abort-request", 147, ((0, "aborted"), (0, 8))),
    ("late-first", 149, ((0, 10), (0, 8))),
    ("late-second", 663, ((1, 10), (256, 14))),
    ("dma-write", 143, ((0, 10), (0, 8))),
    ("deleted-scan0", 651, ((256, 0), (0, 32))),
    ("deleted-scan1", 651, ((256, 0), (0, 42))),
    ("deleted-dma-normal", 652, ((256, 0), (0, 32))),
    ("deleted-dma-both", 652, ((256, 0), (0, 42))),
    ("rewrite-abort", 1180, ((2, "aborted"), (256, 0), (256, 0))),
)
STORAGE_CELLS = (256, 256, 256, 256, 256, 512, 0, 1, 0, 0, 1, 0, 256, 256, 256, 256, 257)

CRC_CASES = tuple(
    (
        name,
        1170,
        ((256, "aborted"), (256, 14 if name.startswith("crc1") or name.endswith("before") else 0)),
    )
    for name in ("crc1-before", "crc1-at", "crc1-after", "crc2-before", "crc2-at", "crc2-after")
)


def raw_writes(flux):
    """Compare final words per callback, retaining clocks, marks, CRCs and gaps."""
    if not isinstance(flux, list) or any(
        not isinstance(row, list)
        or len(row) != 5
        or any(type(value) is not int for value in row)
        or row[0] < 0
        or not 0 <= row[1] < 80
        or not 0 <= row[2] < 3125
        or any(not 0 <= value < 256 for value in row[3:])
        for row in flux
    ):
        raise ValueError("Malformed original raw clock/data cells")
    final = {}
    for epoch, track, cell, data, clocks in flux:
        final[epoch, track, cell] = [epoch, track * 3125 + cell, (clocks << 8) | data]
    return list(final.values())


def validate_cases(metadata, crc=False):
    expected_cases = CRC_CASES if crc else CASES
    expected_cells = (256,) * 6 if crc else STORAGE_CELLS
    basename = "original-crc.json" if crc else "original.json"
    cases = metadata.get("cases", [])
    if (
        metadata.get("own_cold_cases") is not True
        or len(cases) != len(expected_cases)
        or any(
            c.get("name") != name
            or type(c.get("rows")) is not int
            or c["rows"] != rows
            or c.get("fixture") != f"{basename}.{name}.tsv"
            or tuple((t.get("bytes"), t.get("result")) for t in c.get("transfers", [])) != transfers
            for c, (name, rows, transfers) in zip(cases, expected_cases, strict=True)
        )
    ):
        raise ValueError("Incomplete original writable-media histories")
    for case, cells in zip(cases, expected_cells, strict=True):
        raw_writes(case.get("rawFlux"))
        flux = case.get("flux")
        if flux != [
            [epoch, track, cell, data if clocks == 255 else -1]
            for epoch, track, cell, data, clocks in case["rawFlux"]
        ]:
            raise ValueError("Raw cells and original flux observations differ")
        if not isinstance(flux, list) or any(
            not isinstance(row, list)
            or len(row) != 4
            or any(type(value) is not int for value in row)
            or row[0] < 0
            or not 0 <= row[1] < 80
            or not 0 <= row[2] < 3125
            or not -1 <= row[3] <= 255
            for row in flux
        ):
            raise ValueError("Malformed original emitted disc cells")
        final_cells = {
            (epoch, cell): [epoch, 8192 + cell - 667, value]
            for epoch, track, cell, value in flux
            if track == 3 and 667 <= cell <= 922
        }
        expected = list(final_cells.values())
        if len(expected) != cells or case.get("storageWrites") != expected:
            raise ValueError("Original storage projection differs from emitted disc cells")
    return cases


def complete(result, rows):
    return (
        type(rows) is int
        and rows in {case[1] for case in (*CASES, *CRC_CASES)}
        and result.returncode == 0
        and not result.stderr
        and result.stdout == f"{rows} original bus observations; 0 differences\n"
    )


def ignore_marker_damage(text):
    text, count = re.subn(
        r"assign validDataMarker = [^;]+;", "assign validDataMarker = 1'b1;", text
    )
    if count != 1:
        raise ValueError("Missing unique actual damaged-marker predicate")
    return text


def qualify(*, cache=True):
    directory = build.BBC / ("disc-media" if cache else "disc-media-uncached")
    references = [
        ROOT / "tools/bbc/reference_disc_media.mjs",
        ROOT / "tools/bbc/reference_disc_media_crc.mjs",
    ]
    driver = ROOT / "sim/native/bbc_disc_test.cpp"
    sources = [
        Path(__file__),
        *references,
        ROOT / "tools/bbc/reference_disc_media_base.mjs",
        disc_batch.DRIVER,
        disc_batch.HEADER,
        Path(disc_batch.__file__),
        ROOT / "tools/bbc/reference_drive.mjs",
        Path(disc_commands.__file__),
        Path(disc_restart.__file__),
    ]
    inputs = probe.compile_probe(directory, "Intel8271", sources, ["Intel8271"], savable=True)
    before = build.hashes([*sources, driver, build.BBC / "disc.ssd"])
    cases, artifacts = [], []
    for reference, crc in zip(references, (False, True), strict=True):
        original = directory / ("original-crc.json" if crc else "original.json")
        log_path = directory / ("reference-crc.log" if crc else "reference.log")
        with log_path.open("w") as log:
            subprocess.run(
                [
                    "node",
                    str(reference),
                    str(source_root("jsbeeb")),
                    str(build.BBC / "disc.ssd"),
                    str(original),
                ],
                cwd=ROOT,
                stdout=log,
                stderr=subprocess.STDOUT,
                check=True,
                timeout=60,
            )
        cases.extend(validate_cases(json.loads(original.read_text()), crc))
        artifacts.extend([original, log_path])
    results = []
    batches, batch_artifacts, reuse = disc_batch.run(directory, cases, cache=cache)
    artifacts.extend(batch_artifacts)
    for case, run, writes in batches:
        raw = directory / (case["name"] + ".raw.tsv")
        actual_raw = (
            [list(map(int, row.split())) for row in raw.read_text().splitlines()]
            if raw.is_file()
            else None
        )
        actual = (
            [list(map(int, row.split())) for row in writes.read_text().splitlines()]
            if writes.is_file()
            else None
        )
        results.append(
            {
                "name": case["name"],
                "rows": case["rows"],
                "bus_passed": complete(run, case["rows"]),
                "storage_passed": actual == case["storageWrites"],
                "raw_cells_passed": actual_raw == raw_writes(case["rawFlux"]),
            }
        )
        print(f"FDC writable media {case['name']}: {results[-1]}", flush=True)
    negative = {"name": "ignored-marker-damage", "attempted": False, "detected": False}
    if all(r["bus_passed"] and r["storage_passed"] and r["raw_cells_passed"] for r in results):
        fault = directory / "ignored-marker-damage"
        binary = disc_restart.compile_fault(
            fault,
            ignore_marker_damage((directory / "devices.v").read_text()),
            driver,
            "actual damaged-marker predicate disabled only",
        )
        fixture = directory / "original.json.abort-request.tsv"
        writes = fault / "writes.tsv"
        run = subprocess.run(
            [str(binary), str(fixture), str(build.BBC / "disc.ssd"), str(writes)],
            cwd=build.BBC,
            capture_output=True,
            text=True,
            timeout=300,
        )
        log = fault / "result.log"
        log.write_text(run.stdout + run.stderr)
        negative.update(
            attempted=True,
            detected=run.returncode == 1
            and re.fullmatch(r"147 original bus observations; [1-9]\d* differences\n", run.stdout)
            is not None,
        )
        artifacts.extend([writes, log, fault / "devices.v", fault / "build.json"])
    stable = (
        before == build.hashes([*sources, driver, build.BBC / "disc.ssd"])
        and build.generation_current()
        and probe.device_rtl(["Intel8271"]) == (directory / "devices.v").read_text()
    )
    receipt = {
        "passed": stable
        and all(r["bus_passed"] and r["storage_passed"] and r["raw_cells_passed"] for r in results)
        and negative["detected"],
        "inputs": inputs,
        "sources": before,
        "inputs_unchanged": stable,
        "results": results,
        "rows": sum(r["rows"] for r in results),
        "negative_control": negative,
        "prefix_reuse": reuse,
        "artifacts": build.hashes(artifacts),
        "scope": "23 own cold public writable-media histories. Deleted markers, host-reset and late/DMA abort damage, recovery reads, rewrites and resets immediately around both CRC cells. Every accepted bus observation and emitted payload-cell time/address/value compared. Optional own DUT prefix reuse is confined to this process, after successful observations without external writes. No original state enters DUT.",
        "limits": "Standard SSD input with physical raw FM writes. These histories cover runtime marker/CRC damage and recovery; formatting, arbitrary command/media histories and universal game equivalence remain unproved.",
        "universal_game_equivalence": False,
    }
    write_json(directory / "result.json", receipt)
    if not receipt["passed"]:
        raise ValueError("Writable FDC media differs: " + str(directory))
    return receipt


if __name__ == "__main__":
    qualify()
