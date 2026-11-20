"""Compare continued byte service while another command has incomplete parameters."""

import argparse
import json
import re
import subprocess
from pathlib import Path

from tools.validation.common import ROOT, source_root, write_json

from . import build, disc_batch, disc_commands, disc_restart, probe


def expected_names():
    return tuple(
        f"{op:x}-{next_op:x}-{ack}-{complete}"
        for op in (0x53, 0x4B)
        for next_op in (0x53, 0x4B, 0x5B)
        for ack in (0, 1)
        for complete in (0, 1)
        if not complete or next_op == (0x4B if op == 0x53 else 0x53)
    )


def validate_cases(metadata):
    cases = metadata.get("cases", [])
    if (
        metadata.get("own_cold_cases") is not True
        or tuple(case.get("name") for case in cases) != expected_names()
        or any(
            type(case.get("rows")) is not int
            or case["rows"] < 100
            or case.get("fixture") != f"original.json.{case['name']}.tsv"
            or not isinstance(case.get("storageWrites"), list)
            or (case.get("complete") and (case.get("bytes") != 256 or case.get("result") != 0))
            for case in cases
        )
    ):
        raise ValueError("Original active-command histories are incomplete")
    return cases


def early_operation_change(text):
    """Actually disconnect the retained byte-processor operation in generated RTL."""
    old = "assign activeOp = (physicalOperation ? executingOp : op);"
    if text.count(old) != 1:
        raise ValueError("Missing retained-operation fault target")
    return text.replace(old, "assign activeOp = op;")


def qualify(*, cache=True):
    directory = build.BBC / ("disc-active" if cache else "disc-active-uncached")
    driver = ROOT / "sim/native/bbc_disc_test.cpp"
    reference = ROOT / "tools/bbc/reference_disc_active.mjs"
    sources = [
        Path(__file__),
        Path(disc_commands.__file__),
        Path(disc_restart.__file__),
        disc_batch.DRIVER,
        disc_batch.HEADER,
        Path(disc_batch.__file__),
        reference,
        ROOT / "tools/bbc/reference_disc_media_base.mjs",
        ROOT / "tools/bbc/reference_drive.mjs",
    ]
    inputs = probe.compile_probe(directory, "Intel8271", sources, ["Intel8271"], savable=True)
    guarded = [*sources, driver, build.BBC / "disc.ssd"]
    before = build.hashes(guarded)
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
    batches, batch_artifacts, reuse = disc_batch.run(directory, cases, cache=cache)
    artifacts.extend(batch_artifacts)
    for case, run, writes in batches:
        bus = (
            run.returncode == 0
            and not run.stderr
            and run.stdout == f"{case['rows']} original bus observations; 0 differences\n"
        )
        physical = (
            writes.is_file()
            and [list(map(int, row.split())) for row in writes.read_text().splitlines()]
            == case["storageWrites"]
        )
        results.append(
            {
                "name": case["name"],
                "rows": case["rows"],
                "bus_passed": bus,
                "payload_passed": physical,
            }
        )
        print(case["name"], bus, physical, flush=True)
    negative = {"attempted": False, "detected": False}
    if all(r["bus_passed"] and r["payload_passed"] for r in results):
        fault = directory / "early-operation-change"
        binary = disc_restart.compile_fault(
            fault,
            early_operation_change((directory / "devices.v").read_text()),
            driver,
            "actual early byte-processor operation change",
        )
        case = cases[expected_names().index("53-4b-0-0")]
        writes = fault / "writes.tsv"
        run = subprocess.run(
            [
                str(binary),
                str(directory / case["fixture"]),
                str(build.BBC / "disc.ssd"),
                str(writes),
            ],
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
            and re.fullmatch(
                rf"{case['rows']} original bus observations; [1-9]\d* differences\n", run.stdout
            )
            is not None,
        )
        artifacts.extend([writes, log, fault / "devices.v", fault / "build.json"])
    stable = (
        before == build.hashes(guarded)
        and build.generation_current()
        and probe.device_rtl(["Intel8271"]) == (directory / "devices.v").read_text()
    )
    receipt = {
        "passed": stable
        and all(r["bus_passed"] and r["payload_passed"] for r in results)
        and negative["detected"],
        "inputs": inputs,
        "sources": before,
        "inputs_unchanged": stable,
        "results": results,
        "negative_control": negative,
        "prefix_reuse": reuse,
        "artifacts": build.hashes(artifacts),
        "scope": "16 independent own-cold Original public histories. READ/WRITE/READ ID commands with incomplete parameters preserve the prior READ or WRITE. Continued byte service and four complete transfers compare every bus observation and physical payload write with its timestamp. DUT startup may reuse a previously executed identical own prefix within this process; full current generated state is serialized only after matching observations and no external storage writes. An actual early-operation RTL fault must fail. No Original state enters DUT.",
        "universal_game_equivalence": False,
    }
    write_json(directory / "result.json", receipt)
    if not receipt["passed"]:
        raise ValueError("Active FDC command behavior differs: " + str(directory))
    return receipt


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--no-prefix-cache", action="store_true", help="Execute every prefix again")
    qualify(cache=not parser.parse_args().no_prefix_cache)
