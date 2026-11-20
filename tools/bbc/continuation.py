"""Prove and reuse independently executed Elite-A gameplay prefixes."""

from __future__ import annotations

import argparse
import copy
import json
import subprocess
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from pathlib import Path

from tools.validation.common import HERE, ROOT, artifact_hashes, digest, source_root, write_json
from tools.validation.setup import verify

from . import boot_cache, cycle_reference, media
from .capture import capture_pair, native_environment, run, scenario_command
from .compare import compare
from .inputs import analogue_transitions, transitions, write_schedule
from .run import compile_selected, validate_outcomes

BBC = ROOT / "build/bbc"


def split(scenario, at_us):
    """Partition a physical recipe only while all external controls are neutral."""
    if not isinstance(at_us, int) or not 0 < at_us < scenario["duration_us"]:
        raise ValueError("Checkpoint must be inside the scenario")
    events = [e for e in scenario["events"] if e["at_us"] < at_us]
    analogue = [e for e in scenario.get("analogue", []) if e["at_us"] < at_us]
    if events and events[-1]["keys"]:
        raise ValueError("Release every keyboard key before saving a checkpoint")
    if analogue and (analogue[-1]["channels"] != [32768] * 4 or analogue[-1]["buttons"] != 0):
        raise ValueError("Centre the analogue controls and release FIRE before saving")
    parts = []
    for prefix in (True, False):
        offset = 0 if prefix else at_us
        part = copy.deepcopy(scenario)
        part["name"] += "-prefix" if prefix else "-continuation"
        part["duration_us"] = at_us if prefix else scenario["duration_us"] - at_us
        for key in ("events", "analogue", "checkpoints"):
            part[key] = [
                {**e, "at_us": e["at_us"] - offset}
                for e in scenario.get(key, [])
                if (e["at_us"] < at_us) == prefix
            ]
        names = {cp["name"] for cp in part["checkpoints"]}
        if not names:
            raise ValueError("Both parts require an observed gameplay checkpoint")
        part["reference_assertions"] = [
            a for a in scenario.get("reference_assertions", []) if a["checkpoint"] in names
        ]
        transitions(part)
        analogue_transitions(part)
        parts.append(part)
    return parts


def recipe(scenario):
    # A new branch may have a different name but must execute the same prefix.
    return {k: v for k, v in scenario.items() if k not in {"name", "notes"}}


def provenance(context=None):
    if not boot_cache.valid(context=context):
        raise ValueError("Both independently executed own-machine boots must be verified")
    return {
        "boots": {
            b: {
                "inputs": boot_cache.inputs(b, context),
                "states": boot_cache.state_hashes(b, context),
            }
            for b in boot_cache.backends(context)
        },
        "observers": {
            str(p.relative_to(ROOT)): digest(p)
            for p in sorted(
                [*ROOT.glob("tools/bbc/*.py"), *ROOT.glob("tools/bbc/*.mjs")]
                + [HERE / n for n in ("common.py", "compare.py", "run.py", "setup.py")]
            )
        },
    }


def capture(directory, scenario, states, save=False, context=None):
    directory.mkdir()
    write_schedule(directory, scenario, analogue=True)
    config = {
        **(boot_cache.REFERENCE_OPTIONS if context is None else context.reference_options),
        "cycle_library": str(cycle_reference.BINARY),
        "jsbeeb": str(source_root("jsbeeb")),
        "disc": str(boot_cache.disc_path(context)),
        "load_state": str(states["reference"]),
        "output": str(directory / "reference"),
        "scenario": scenario,
        "trace_bus": True,
    }
    if save:
        config["save_scenario_state"] = str(directory / "reference" / "own.state")
    write_json(directory / "reference.json", config)
    write_json(directory / "scenario.json", scenario)
    with ThreadPoolExecutor(max_workers=2) as executor:
        capture_pair(
            executor,
            partial(
                run,
                scenario_command(BBC / "native/VBbcMachine", directory, scenario, "dut"),
                BBC,
                directory / "dut.log",
                native_environment(
                    load=states["dut"],
                    save=directory / "dut" / "own.state" if save else None,
                    trace=True,
                    analogue=directory / "analogue.tsv",
                ),
                timeout=max(600, scenario["duration_us"] // 25000),
            ),
            partial(
                run,
                ["node", str(ROOT / "tools/bbc/reference.mjs"), str(directory / "reference.json")],
                ROOT,
                directory / "reference.log",
                timeout=max(600, scenario["duration_us"] // 25000),
            ),
        )
    result = compare(directory, scenario)
    result["artifact_hashes"] = artifact_hashes(directory)
    write_json(directory / "comparison.json", result)
    print(f"{directory.name}: {'PASS' if result['passed'] else 'FAIL'}", flush=True)
    return result


def equal_tail(full, tail, offset):
    """Compare exact bytes in bounded memory, including unequal file lengths."""
    with full.open("rb") as left, tail.open("rb") as right:
        left.seek(offset)
        while True:
            a, b = left.read(1024 * 1024), right.read(1024 * 1024)
            if a != b:
                return False
            if not a:
                return True


def continuation_checks(continuous, suffix, scenario, at_us):
    checks = {}
    for backend, clock in (("dut", 32), ("reference", 2)):
        a, b = continuous / backend, suffix / backend
        for cp in scenario["checkpoints"]:
            for ext in ("ram", "ssd", "rgb" if backend == "dut" else "rgba"):
                name = f"{cp['name']}.{ext}"
                checks[f"{backend}/{name}"] = equal_tail(a / name, b / name, 0)
        checks[f"{backend}/disc-output.ssd"] = equal_tail(
            a / "disc-output.ssd", b / "disc-output.ssd", 0
        )
        audio = (
            [("audio.u8", 1)]
            if backend == "dut"
            else [("audio-digital.u8", 1), ("audio-pins.u8", 1), ("audio.f32", 4)]
        )
        for name, width in audio:
            checks[f"{backend}/{name}"] = equal_tail(a / name, b / name, at_us // 2 * width)
        for name in ("frames.tsv", "field-hashes.tsv", "psg.tsv", "bus.tsv"):
            offset = at_us * clock
            expected = []
            for line in (a / name).read_text().splitlines():
                row = line.split()
                if row and int(row[0]) > offset:
                    expected.append([str(int(row[0]) - offset), *row[1:]])
            actual = [line.split() for line in (b / name).read_text().splitlines() if line.strip()]
            checks[f"{backend}/{name}"] = expected == actual
    return checks


def load_prefix(receipt_path, prefix, context=None):
    receipt = json.loads(receipt_path.read_text())
    if (
        receipt.get("schema") != 1
        or receipt.get("prefix_recipe") != recipe(prefix)
        or receipt.get("provenance") != (provenance() if context is None else provenance(context))
        or receipt.get("passed") is not True
        or receipt.get("repeatable") is not True
        or receipt.get("continuous_equivalence") is not True
    ):
        raise ValueError("Gameplay checkpoint is stale, incompatible or unproved")
    receipt_path = receipt_path.resolve()
    directory = receipt_path.parent
    present = {
        str(p.relative_to(directory))
        for p in directory.rglob("*")
        if p.is_file() and p != receipt_path
    }
    if set(receipt.get("sealed_files", {})) != present:
        raise ValueError("Gameplay checkpoint evidence is missing or unsealed")
    required = {
        "prefix-1/dut/own.state",
        "prefix-1/dut/own.state.ssd",
        "prefix-1/dut/own.state.rgb",
        "prefix-1/reference/own.state",
        "prefix-2/dut/own.state",
        "prefix-2/reference/own.state",
        *[
            f"{part}-{n}/comparison.json"
            for part in ("prefix", "continuous", "suffix")
            for n in (1, 2)
        ],
    }
    if not required <= present:
        raise ValueError("Gameplay checkpoint lacks repeated independent execution evidence")
    for name, expected in receipt["sealed_files"].items():
        path = (directory / name).resolve()
        if not path.is_relative_to(directory.resolve()) or digest(path) != expected:
            raise ValueError("Gameplay checkpoint evidence or own state changed")
    return {b: directory / "prefix-1" / b / "own.state" for b in ("dut", "reference")}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", required=True)
    parser.add_argument("--split-us", type=int, required=True)
    parser.add_argument("--reuse-prefix", type=Path)
    parser.add_argument(
        "--jobs",
        type=int,
        choices=[2, 4],
        default=4,
        help="Backend process limit; overlap independent continuous and prefix captures",
    )
    args = parser.parse_args()
    contract = json.loads((HERE / "scenarios.json").read_text())
    extra = json.loads((ROOT / "tools/bbc/scenarios.json").read_text())
    contract["actions"].update(extra["actions"])
    contract["scenarios"].extend(extra["scenarios"])
    scenario = compile_selected(contract, [args.scenario])[0]
    prefix, suffix = split(scenario, args.split_us)
    verify()
    validate_outcomes([scenario])
    boot_cache.configure()
    from . import adc_results

    adc_results.prepare()
    preset = scenario.get("media_fixture")
    context = (
        None
        if preset is None
        else boot_cache.media_profile(
            media.prepare(preset),
            {**boot_cache.REFERENCE_OPTIONS, "cycle_library": str(cycle_reference.BINARY)},
        )
    )
    before = provenance(context)
    cached = load_prefix(args.reuse_prefix, prefix, context) if args.reuse_prefix else None
    root = BBC / "continuations"
    root.mkdir(exist_ok=True)
    output = Path(tempfile.mkdtemp(prefix="run-", dir=root))
    write_json(output / "scenario.json", scenario)
    print(output, flush=True)
    started = time.monotonic()
    boots = {b: boot_cache.backends(context)[b][0] for b in ("dut", "reference")}
    results, checks = [], []
    error = None
    try:
        if cached:
            with ThreadPoolExecutor(max_workers=args.jobs // 2) as executor:
                futures = [
                    executor.submit(
                        capture, output / f"suffix-{n}", suffix, cached, context=context
                    )
                    for n in (1, 2)
                ]
                results.extend(future.result() for future in futures)
        else:
            for repetition in (1, 2):
                p, c = output / f"prefix-{repetition}", output / f"continuous-{repetition}"
                with ThreadPoolExecutor(max_workers=args.jobs // 2) as executor:
                    pf = executor.submit(capture, p, prefix, boots, save=True, context=context)
                    cf = executor.submit(capture, c, scenario, boots, context=context)
                    results.append(pf.result())
                    states = {b: p / b / "own.state" for b in ("dut", "reference")}
                    sf = executor.submit(
                        capture, output / f"suffix-{repetition}", suffix, states, context=context
                    )
                    results.append(cf.result())
                    results.append(sf.result())
                checks.append(
                    continuation_checks(c, output / f"suffix-{repetition}", suffix, args.split_us)
                )
                continue
    except (OSError, ValueError, LookupError, subprocess.SubprocessError) as failure:
        # Keep completed and partial captures reviewable, but never seal a
        # failed or misaligned save as a reusable gameplay prefix.
        error = str(failure)
    groups = ["suffix"] if cached else ["prefix", "continuous", "suffix"]
    repeatable = all(
        artifact_hashes(output / f"{group}-1") == artifact_hashes(output / f"{group}-2")
        for group in groups
    )
    stable = before == provenance(context)
    passed = (
        error is None
        and len(results) == (2 if cached else 6)
        and stable
        and repeatable
        and all(r["passed"] for r in results)
        and all(all(check.values()) for check in checks)
    )
    report = {
        "schema": 1,
        "capture_process_limit": args.jobs,
        "error": error,
        "passed": passed,
        "repeatable": repeatable,
        "continuous_equivalence": not cached and passed,
        "dut_executed_this_run": True,
        "full_game_equivalence": False,
        "prefix_recipe": recipe(prefix),
        "provenance": before,
        "source_prefix_receipt": str(args.reuse_prefix) if cached else None,
        "source_prefix_receipt_sha256": digest(args.reuse_prefix) if cached else None,
        "split_us": args.split_us,
        "continuous_checks": checks,
        "capture_results": results,
        "wall_seconds": time.monotonic() - started,
    }
    if not cached:
        report["sealed_files"] = {
            str(p.relative_to(output)): digest(p) for p in sorted(output.rglob("*")) if p.is_file()
        }
    write_json(output / "report.json", report)
    print(f"{'PASS' if passed else 'FAIL'}: {output / 'report.json'}", flush=True)
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
