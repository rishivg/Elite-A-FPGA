"""Recheck stronger outcomes from qualified BBC captures without guest execution.

RTL, reference code, tools and captured bytes must remain unchanged. Only adding
outcome assertions is allowed; changed inputs or removed assertions need replay.
Seal the original contracts while their bytes still match the capture manifest.
"""

from __future__ import annotations

import argparse
import json
import tempfile
import time
from collections import Counter
from pathlib import Path

from tools.bbc.build import native_current
from tools.bbc.compare import compare
from tools.bbc.cycle_reference import current as cycle_current
from tools.bbc.run import summarize_repetitions

from .common import CACHE, ROOT, artifact_hashes, digest, write_json
from .run import compile_scenario
from .setup import verify

CONTRACTS = ("tools/validation/scenarios.json", "tools/bbc/scenarios.json")
TARGET = "Elite-A released, Model B DFS1.2, no Tube"


def read(path: Path) -> dict:
    return json.loads(path.read_text())


def assertion_counts(scenario: dict) -> Counter:
    return Counter(json.dumps(a, sort_keys=True) for a in scenario.get("reference_assertions", []))


def runtime_current(clock_multiplier=1) -> None:
    verify()
    valid = native_current() if clock_multiplier == 1 else native_current(clock_multiplier)
    if not valid or not cycle_current():
        raise ValueError("Capture runtime is stale; rebuild and replay")


def runtime_for(manifest: dict) -> None:
    clock = manifest.get("master_clock_hz", 32000000)
    if clock not in {32000000, 96000000}:
        raise ValueError("Unsupported captured master clock")
    if clock == 32000000:
        runtime_current()
    else:
        runtime_current(3)


def source_hashes(manifest: dict) -> dict:
    if manifest.get("target") != TARGET or not manifest.get("inputs"):
        raise ValueError("Missing original-game capture provenance")
    hashes = {}
    for name, expected in manifest["inputs"].items():
        path = Path(name)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("Invalid capture source path")
        if name in CONTRACTS:
            continue
        actual = digest(ROOT / path)
        if actual != expected:
            raise ValueError("Capture source changed: " + name)
        hashes[name] = actual
    return hashes


def seal_contracts(run: Path) -> None:
    manifest = read(run / "manifest.json")
    source_hashes(manifest)
    for name in CONTRACTS:
        expected = manifest["inputs"][name]
        snapshot = run / "contract-sources" / name
        if snapshot.exists():
            if digest(snapshot) != expected:
                raise ValueError("Captured contract snapshot changed: " + name)
            continue
        source = ROOT / name
        if digest(source) != expected:
            raise ValueError("Original contract unavailable; replay before changing assertions")
        snapshot.parent.mkdir(parents=True, exist_ok=True)
        snapshot.write_bytes(source.read_bytes())
        if digest(snapshot) != expected:
            raise ValueError("Contract changed during snapshot")


def scenarios(run: Path, manifest: dict) -> tuple[list[dict], list[dict]]:
    original, current = [], []
    old_actions, new_actions = {}, {}
    for name in CONTRACTS:
        snapshot = run / "contract-sources" / name
        if digest(snapshot) != manifest["inputs"][name]:
            raise ValueError("Captured contract snapshot changed: " + name)
        old, new = read(snapshot), read(ROOT / name)
        if {k: v for k, v in old.items() if k != "scenarios"} != {
            k: v for k, v in new.items() if k != "scenarios"
        }:
            raise ValueError("Capture actions or contract format changed")
        if len(old["scenarios"]) != len(new["scenarios"]):
            raise ValueError("Scenario set changed; replay required")
        for before, after in zip(old["scenarios"], new["scenarios"]):
            if {k: v for k, v in before.items() if k != "reference_assertions"} != {
                k: v for k, v in after.items() if k != "reference_assertions"
            }:
                raise ValueError("Capture schedule changed: " + before["name"])
            if assertion_counts(before) - assertion_counts(after):
                raise ValueError("Outcome assertions removed or weakened: " + before["name"])
        original.extend(old["scenarios"])
        current.extend(new["scenarios"])
        old_actions.update(old["actions"])
        new_actions.update(new["actions"])

    def compile_all(raw, actions):
        result = [compile_scenario(s, actions) for s in raw]
        for s in result:
            s["unsupported_actions"] = []
            s["dut_assertions"] = []
        return result

    return compile_all(original, old_actions), compile_all(current, new_actions)


def recheck(run: Path) -> dict:
    started = time.monotonic()
    manifest, saved = read(run / "manifest.json"), read(run / "report.json")
    runtime_for(manifest)
    before = source_hashes(manifest)
    if not all(
        saved.get(k) is True
        for k in ["strict_matrix_passed", "boot_provenance_complete", "inputs_unchanged"]
    ):
        raise ValueError(
            "Only a complete, repeated, source-verified strict matrix can be rechecked"
        )
    if manifest.get("reference_options", {}).get("reference_cpu") != "cycle":
        raise ValueError("Recheck requires the qualified cycle reference")
    seal_contracts(run)
    original, current = scenarios(run, manifest)
    if not original or [r["name"] for r in saved["results"]] != [s["name"] for s in original]:
        raise ValueError("Missing, extra or reordered captured scenarios")
    report_hash, manifest_hash = digest(run / "report.json"), digest(run / "manifest.json")
    contracts_before = {name: digest(ROOT / name) for name in CONTRACTS}
    results = []
    for old, new, receipt in zip(original, current, saved["results"]):
        expected = receipt.get("artifact_hashes")
        repetitions_receipt = receipt.get("repetitions", [])
        if (
            receipt.get("passed") is not True
            or receipt.get("repeatable") is not True
            or len(repetitions_receipt) != 2
            or any(item.get("passed") is not True for item in repetitions_receipt)
            or not expected
        ):
            raise ValueError("Capture did not pass twice: " + old["name"])
        repetitions = []
        for suffix in ["", "-repeat"]:
            directory = run / (old["name"] + suffix)
            if read(directory / "scenario.json") != old:
                raise ValueError("Recorded scenario changed: " + directory.name)
            case = read(directory / "comparison.json")
            if case.get("passed") is not True or case.get("artifact_hashes") != expected:
                raise ValueError("Capture receipt changed: " + directory.name)
            if artifact_hashes(directory) != expected:
                raise ValueError("Captured bytes changed: " + directory.name)
            result = compare(directory, new)
            if artifact_hashes(directory) != expected:
                raise ValueError("Capture changed during comparison: " + directory.name)
            result["artifact_hashes"] = expected
            repetitions.append(result)
        results.append(summarize_repetitions(repetitions))
    runtime_for(manifest)
    if (
        source_hashes(manifest) != before
        or contracts_before != {name: digest(ROOT / name) for name in CONTRACTS}
        or report_hash != digest(run / "report.json")
        or manifest_hash != digest(run / "manifest.json")
    ):
        raise ValueError("Inputs changed during recheck")
    return {
        "scope": "Comparison-only recheck of qualified original-game captures",
        "strict_matrix_passed": all(r["passed"] and r["repeatable"] for r in results),
        "dut_executed_this_run": False,
        "source_capture_run": str(run),
        "source_report_sha256": report_hash,
        "source_manifest_sha256": manifest_hash,
        "comparison_tool_sha256": digest(Path(__file__)),
        "assertion_contract_hashes": contracts_before,
        "remaining": saved["remaining"],
        "results": results,
        "seconds": time.monotonic() - started,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    parser.add_argument(
        "--seal-contracts",
        action="store_true",
        help="Preserve manifest-checked contracts before editing assertions; no comparisons or executions",
    )
    args = parser.parse_args()
    run = args.run.resolve(strict=True)
    if args.seal_contracts:
        seal_contracts(run)
        print("Sealed original capture contracts; no guest execution or comparison")
        return 0
    result = recheck(run)
    parent = CACHE / "bbc-rechecks"
    parent.mkdir(parents=True, exist_ok=True)
    output = Path(tempfile.mkdtemp(prefix="run-", dir=parent))
    write_json(output / "report.json", result)
    print(
        f"Rechecked {len(result['results'])} scenarios in {result['seconds']:.2f}s; strict comparisons passed={result['strict_matrix_passed']}; {output}/report.json"
    )
    return 0 if result["strict_matrix_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
