"""Recompare complete BBC captures after a change to the outcome decoder.

Seal the observations while every original capture source still matches. Only
tools/bbc/compare.py may subsequently change. Capture sources, contracts, own
boots and original observations remain immutable; derived images and state JSON
are written in a separate directory. This never executes either guest.
"""

from __future__ import annotations

import argparse
import json
import tempfile
import time
from pathlib import Path

from tools.bbc import boot_cache
from tools.bbc.compare import compare
from tools.bbc.run import compile_selected, summarize_repetitions

from .bbc_recheck import CONTRACTS, TARGET, read, runtime_for
from .common import CACHE, ROOT, artifact_hashes, digest, write_json

COMPARATOR = "tools/bbc/compare.py"
SEAL = "observation-seal.json"


def sources(manifest, *, allow_decoder=False):
    if manifest.get("target") != TARGET or not manifest.get("inputs"):
        raise ValueError("Missing original-game capture provenance")
    hashes = {}
    for name, expected in manifest["inputs"].items():
        path = Path(name)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("Invalid capture source path")
        actual = digest(ROOT / path)
        if actual != expected and not (allow_decoder and name == COMPARATOR):
            raise ValueError("Capture source changed: " + name)
        hashes[name] = actual
    return hashes


def own_boots(manifest):
    options = manifest.get("reference_options", {})
    if any(
        options.get(key) != value
        for key, value in {
            "reference_cpu": "cycle",
            "drive_profile": "clocked-fm",
            "power_profile": "shared-reset",
        }.items()
    ):
        raise ValueError("Requires the independent cycle reference and declared reset fixture")
    adc_profile = options.get("adc_profile", "original")
    if adc_profile not in {"original", "bbc-result-bits"}:
        raise ValueError("Unknown captured ADC result profile")
    clock = manifest.get("master_clock_hz", 32000000)
    if clock not in {32000000, 96000000}:
        raise ValueError("Unsupported captured master clock")
    arguments = {} if clock == 32000000 else {"clock_multiplier": 3}
    boot_cache.configure(
        options["reference_cpu"],
        options["drive_profile"],
        options["power_profile"],
        adc_profile,
        **arguments,
    )
    if not boot_cache.valid():
        raise ValueError("Own-machine boots are stale or unverified")
    return digest(boot_cache.receipt_path())


def scenarios(run):
    shared, bbc = [read(ROOT / name) for name in CONTRACTS]
    contract = {
        "actions": shared["actions"] | bbc["actions"],
        "scenarios": shared["scenarios"] + bbc["scenarios"],
    }
    compiled = compile_selected(contract)
    expected = {s["name"] + suffix for s in compiled for suffix in ["", "-repeat"]}
    actual = {p.name for p in run.iterdir() if p.is_dir() and (p / "scenario.json").exists()}
    if actual != expected:
        raise ValueError("Requires every scenario captured twice; missing or extra cases")
    for scenario in compiled:
        for suffix in ["", "-repeat"]:
            directory = run / (scenario["name"] + suffix)
            if read(directory / "scenario.json") != scenario:
                raise ValueError("Recorded scenario changed: " + directory.name)
            rs = read(directory / "reference/summary.json")
            ds = read(directory / "dut/summary.json")
            if (
                rs.get("completed") is not True
                or ds.get("completed") is not True
                or ds.get("duration_cycles") != scenario["duration_us"] * 32
                or not scenario["duration_us"] * 2
                <= rs.get("duration_cycles", -1)
                <= scenario["duration_us"] * 2 + 32
            ):
                raise ValueError("Incomplete capture: " + directory.name)
    return compiled


def observations(run, compiled):
    return {
        scenario["name"] + suffix: artifact_hashes(run / (scenario["name"] + suffix))
        for scenario in compiled
        for suffix in ["", "-repeat"]
    }


def seal(run):
    """Bind complete observations before changing the pure comparison code."""
    manifest = read(run / "manifest.json")
    runtime_for(manifest)
    before = sources(manifest)
    boots = own_boots(manifest)
    compiled = scenarios(run)
    captured = observations(run, compiled)
    manifest_hash = digest(run / "manifest.json")
    if (run / SEAL).exists():
        saved = read(run / SEAL)
        if (
            saved.get("manifest_sha256") != manifest_hash
            or saved.get("observations") != captured
            or saved.get("own_boot_receipt_sha256") != boots
        ):
            raise ValueError("Sealed observations changed")
        return saved
    if (
        sources(manifest) != before
        or own_boots(manifest) != boots
        or observations(run, compiled) != captured
        or digest(run / "manifest.json") != manifest_hash
    ):
        raise ValueError("Capture changed while sealing")
    result = {
        "schema": 1,
        "manifest_sha256": manifest_hash,
        "own_boot_receipt_sha256": boots,
        "capture_sources": before,
        "observations": captured,
        "sealing_tool_sha256": digest(Path(__file__)),
    }
    write_json(run / SEAL, result)
    return result


def derived(name):
    # These files are produced by the Python comparator, not either machine.
    return name.endswith(".state.json") or name.endswith(".png")


def compared_artifacts(directory, original_hashes, links):
    """Reuse authenticated raw hashes; hash only newly decoded output files."""
    hashes = {}
    observed_links = set()
    for backend in ["reference", "dut"]:
        for path in sorted((directory / backend).iterdir()):
            name = str(path.relative_to(directory))
            if derived(path.name):
                hashes[name] = digest(path)
            else:
                if path not in links or not path.is_symlink() or path.resolve() != links[path]:
                    raise ValueError("Borrowed observation changed: " + name)
                observed_links.add(path)
                hashes[name] = original_hashes[name]
    if observed_links != set(links):
        raise ValueError("Missing borrowed observation")
    return hashes


def repaired_scenarios(compiled, repairs):
    """Apply explicit assertion replacements; physical inputs cannot be edited."""
    if not isinstance(repairs, list):
        raise ValueError("Outcome repairs must be an array")
    compiled = json.loads(json.dumps(compiled))
    for repair in repairs:
        if (
            not isinstance(repair, dict)
            or set(repair) != {"scenario", "reason", "before", "after"}
            or not isinstance(repair["reason"], str)
            or not repair["reason"].strip()
        ):
            raise ValueError("Outcome repair needs a reason and exact before/after assertions")
        before, after = repair["before"], repair["after"]
        if (
            not isinstance(before, dict)
            or not isinstance(after, dict)
            or set(before) != set(after)
            or set(before)
            not in [{"checkpoint", "field", "equals"}, {"checkpoint", "field", "at_least"}]
            or before["checkpoint"] != after["checkpoint"]
        ):
            raise ValueError("Outcome repairs can only replace an assertion field or value")
        matches = [s for s in compiled if s["name"] == repair["scenario"]]
        if len(matches) != 1:
            raise ValueError("Unknown outcome repair scenario")
        assertions = matches[0].get("reference_assertions", [])
        if assertions.count(before) != 1 or before == after:
            raise ValueError("Outcome repair must name exactly one existing, changed assertion")
        assertions[assertions.index(before)] = after
    return compiled


def recompare(run, output, repairs=None):
    started = time.monotonic()
    manifest, saved = read(run / "manifest.json"), read(run / SEAL)
    runtime_for(manifest)
    seal_hash = digest(run / SEAL)
    tool_hash = digest(Path(__file__))
    before = sources(manifest, allow_decoder=True)
    boots = own_boots(manifest)
    if (
        saved.get("schema") != 1
        or saved.get("manifest_sha256") != digest(run / "manifest.json")
        or saved.get("capture_sources") != manifest["inputs"]
        or saved.get("own_boot_receipt_sha256") != boots
    ):
        raise ValueError("Invalid observation seal or changed provenance")
    compiled = scenarios(run)
    captured = observations(run, compiled)
    if captured != saved["observations"]:
        raise ValueError("Original observations changed")
    repairs = [] if repairs is None else repairs
    compared = repaired_scenarios(compiled, repairs)
    results = []
    for scenario in compared:
        repetitions = []
        for suffix in ["", "-repeat"]:
            name = scenario["name"] + suffix
            directory = output / name
            directory.mkdir(parents=True, exist_ok=False)
            links = {}
            for backend in ["reference", "dut"]:
                (directory / backend).mkdir()
                for path in sorted((run / name / backend).iterdir()):
                    if path.is_file() and not derived(path.name):
                        destination = directory / backend / path.name
                        links[destination] = path.resolve()
                        destination.symlink_to(links[destination])
            write_json(directory / "scenario.json", scenario)
            result = dict(compare(directory, scenario))
            result["artifact_hashes"] = compared_artifacts(directory, captured[name], links)
            write_json(directory / "comparison.json", result)
            repetitions.append(result)
        results.append(summarize_repetitions(repetitions))
    if (
        observations(run, compiled) != captured
        or scenarios(run) != compiled
        or sources(manifest, allow_decoder=True) != before
        or own_boots(manifest) != boots
        or digest(run / "manifest.json") != saved["manifest_sha256"]
        or digest(run / SEAL) != seal_hash
        or digest(Path(__file__)) != tool_hash
    ):
        raise ValueError("Inputs changed during recomparison")
    return {
        "scope": "Comparison-only evaluation of complete, sealed original-game observations",
        "strict_matrix_passed": all(r["passed"] and r["repeatable"] for r in results),
        "dut_executed_this_run": False,
        "capture_sources_unchanged": True,
        "original_observations_unchanged": True,
        "boot_provenance_complete": True,
        "source_capture_run": str(run),
        "source_manifest_sha256": saved["manifest_sha256"],
        "observation_seal_sha256": seal_hash,
        "original_comparator_sha256": manifest["inputs"][COMPARATOR],
        "current_comparator_sha256": before[COMPARATOR],
        "recomparison_tool_sha256": tool_hash,
        "changed_source_paths": [
            name for name in before if before[name] != manifest["inputs"][name]
        ],
        "outcome_repairs": repairs,
        "results": results,
        "seconds": time.monotonic() - started,
        "passed": False,
        "remaining": [
            "Further combat, successful delivery, galactic travel and economic boundary outcomes",
            "Physical board integration, routed timing and analogue audio/video captures",
            "Remaining CPU, ADC, disc controller and video corner cases",
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--seal", action="store_true")
    parser.add_argument(
        "--outcome-repairs",
        type=Path,
        help="JSON array of explicit assertion replacements with reasons; physical inputs remain sealed",
    )
    args = parser.parse_args()
    run = args.run.resolve()
    if args.seal:
        if args.outcome_repairs:
            raise ValueError("Seal original observations before declaring outcome repairs")
        seal(run)
        print("Sealed complete observations: " + str(run / SEAL))
        return 0
    parent = CACHE / "bbc-recomparisons"
    parent.mkdir(parents=True, exist_ok=True)
    output = Path(tempfile.mkdtemp(prefix="run-", dir=parent))
    repairs = read(args.outcome_repairs) if args.outcome_repairs else []
    repairs_hash = digest(args.outcome_repairs) if args.outcome_repairs else None
    report = recompare(run, output, repairs)
    if args.outcome_repairs and digest(args.outcome_repairs) != repairs_hash:
        raise ValueError("Outcome repairs changed during recomparison")
    report["outcome_repairs_sha256"] = repairs_hash
    write_json(output / "report.json", report)
    print("Report: " + str(output / "report.json"))
    return 0 if report["strict_matrix_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
