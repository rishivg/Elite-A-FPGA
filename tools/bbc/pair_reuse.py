"""Recover completed replay captures without executing either machine again."""

from __future__ import annotations

import json
import shutil
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from tools.validation.common import ROOT, digest, write_json

from . import boot_cache, boot_reuse
from .compare import compare
from .reference_reuse import file_hashes


def read(path):
    return json.loads(path.read_text())


def capture_name(name, repetition):
    return name + ("-repeat" if repetition else "")


def capture_hashes(directory):
    if any((directory / backend).is_symlink() for backend in ("reference", "dut")):
        raise ValueError("Prior captures must have independent directories")
    return {
        f"{backend}/{name}": value
        for backend in ("reference", "dut")
        for name, value in file_hashes(directory / backend).items()
    }


def matching_boots(old, new, workspace):
    expected = new["independent_boot_receipt"]["backends"]
    observed = old["independent_boot_receipt"]["backends"]
    return (
        expected.keys() == observed.keys() == {"dut", "reference"}
        and all(
            record["independent_execution"]
            and all(record[key] == expected[backend][key] for key in ("inputs", "states"))
            for backend, record in observed.items()
        )
        and old["hashes"]
        == {
            str(workspace / name): value
            for record in observed.values()
            for name, value in record["states"].items()
        }
        and all(
            Path(name).resolve().is_relative_to(workspace) and digest(Path(name)) == value
            for name, value in old["hashes"].items()
        )
    )


def run_workspace(source):
    if len(source.parents) < 4:
        raise ValueError("Expected a BBC run under its own workspace's build/bbc/runs")
    workspace = source.parents[3]
    if source.parent != workspace / "build/bbc/runs":
        raise ValueError("Expected a BBC run under its own workspace's build/bbc/runs")
    return workspace


def verify_source(source, manifest):
    """Compare the replay recipe before either runner needs to boot a machine."""
    source = source.resolve(strict=True)
    workspace = run_workspace(source)
    old = read(source / "manifest.json")
    keys = (
        "target",
        "master_clock_hz",
        "clock_hz",
        "repetitions",
        "capture_storage",
        "trace_bus",
        "compiler_profile",
        "inputs",
    )
    if any(old.get(key) != manifest[key] for key in keys) or old["repetitions"] != 2:
        raise ValueError("Prior pairs require identical replay inputs, clocks and capture options")
    if old.get("reference_observations_source") or manifest.get("reference_observations_source"):
        raise ValueError("Pair reuse requires a run with independently executed reference captures")
    if {k: v for k, v in old["reference_options"].items() if k != "cycle_library"} != {
        k: v for k, v in manifest["reference_options"].items() if k != "cycle_library"
    }:
        raise ValueError("Prior pairs used different reference options")
    if any(
        not (workspace / name).resolve().is_relative_to(workspace)
        or digest(workspace / name) != value
        for name, value in old["inputs"].items()
    ):
        raise ValueError("Prior replay inputs have changed")
    return source, old


def completed(source, old, snapshots):
    """Find published passing captures with the same own-machine boots."""
    workspace = source.parents[3]
    old_boots = read(source / "boot-snapshots.json")["media_profiles"]
    if any(
        name not in old_boots or not matching_boots(old_boots[name], snapshot, workspace)
        for name, snapshot in snapshots.items()
    ):
        raise ValueError("Prior pairs require the same verified own-machine boots")
    progress = source / "progress.json"
    published = read(progress)["results"] if progress.is_file() else []
    finished = {row["name"]: row for row in published}
    recovered = {}
    for name in old["scenarios"]:
        if name in finished:
            row = finished[name]
            if (
                row["passed"] is True
                and row["repeatable"] is True
                and row["repetitions"] == [{"passed": True}] * 2
            ):
                recovered[name] = (0, 1)
        else:
            captures = []
            for repetition in range(2):
                path = source / capture_name(name, repetition) / "comparison.json"
                try:
                    receipt = read(path)
                except (OSError, ValueError):
                    continue
                if (
                    isinstance(receipt, dict)
                    and receipt.get("passed") is True
                    and receipt.get("artifact_hashes")
                ):
                    captures.append(repetition)
            if captures:
                recovered[name] = tuple(captures)
    return recovered


def recover_boots(source, contexts, reference_options):
    """Restore the prior verified own boots before falling back to cold execution."""
    source = source.resolve(strict=True)
    workspace = run_workspace(source)
    bundle = workspace.parent
    if workspace == ROOT or not (bundle / "execution-bundle.json").is_file():
        return
    for name, context in contexts.items():
        if boot_cache.valid(context=context):
            continue
        medium = name or "released"
        try:
            boot_reuse.reuse(
                bundle,
                sealed_boots_only=True,
                run=source.relative_to(workspace),
                clock_multiplier=boot_cache.clock_multiplier(context),
                context=context,
                reference_options=reference_options,
            )
        except (KeyError, ValueError, OSError) as error:
            print(f"Using the normal boot path for {medium}: {error}", flush=True)


def copy_captures(source, output, scenario, repetitions, prepare):
    """Verify archived observations, comparing equal repetitions only once."""
    names = [capture_name(scenario["name"], index) for index in repetitions]
    with tempfile.TemporaryDirectory(prefix="pair-", dir=output) as staging:
        staged = Path(staging)
        expected = None
        for repetition, case_name in zip(repetitions, names, strict=True):
            prior = source / case_name
            if prior.is_symlink() or read(prior / "scenario.json") != scenario:
                raise ValueError("Prior scenario has changed")
            receipt = read(prior / "comparison.json")
            hashes = receipt.get("artifact_hashes") if isinstance(receipt, dict) else None
            if (
                not isinstance(hashes, dict)
                or not hashes
                or receipt.get("passed") is not True
                or receipt.get("name") != scenario["name"]
            ):
                raise ValueError("Missing passing comparison of both machines")
            if expected is not None and hashes != expected:
                raise ValueError("Prior capture receipts differ between repetitions")
            expected = hashes
            destination, _ = prepare(scenario, repetition, staged)
            for backend in ("reference", "dut"):
                observations = prior / backend
                if observations.is_symlink() or any(
                    path.is_symlink() or not path.is_file() for path in observations.iterdir()
                ):
                    raise ValueError(
                        "Prior captures must contain only independent observation files"
                    )
                shutil.copytree(observations, destination / backend)
            if capture_hashes(destination) != hashes:
                raise ValueError("Copied captures changed")
        comparison = compare(staged / names[0], scenario)
        if not comparison["passed"] or capture_hashes(staged / names[0]) != expected:
            raise ValueError("Prior comparison could not be reproduced")
        comparison["artifact_hashes"] = expected
        for case_name in names[1:]:
            for path in (staged / names[0]).glob("*.comparison.png"):
                shutil.copyfile(path, staged / case_name / path.name)
        for case_name in names:
            write_json(staged / case_name / "comparison.json", comparison)
            write_json(
                staged / case_name / "pair-reuse.json",
                {"source_case": str(source / case_name), "same_replay_inputs": True},
            )
        for case_name in names:
            (staged / case_name).rename(output / case_name)
    return {(scenario["name"], index): comparison for index in repetitions}


def recover(source, output, scenarios, available, prepare, jobs):
    """Verify archived scenarios in parallel with separate staging directories."""
    eligible = sorted(
        (s for s in scenarios if available.get(s["name"])),
        key=lambda scenario: -scenario["duration_us"],
    )
    if not eligible:
        return {}
    recovered = {}
    with ThreadPoolExecutor(max_workers=min(jobs, len(eligible))) as pool:
        pending = {
            pool.submit(
                copy_captures, source, output, scenario, available[scenario["name"]], prepare
            ): scenario["name"]
            for scenario in eligible
        }
        for future in as_completed(pending):
            name = pending[future]
            try:
                recovered.update(future.result())
            except (KeyError, ValueError, OSError) as error:
                print(f"Replaying {name} after capture reuse failed: {error}", flush=True)
            else:
                indices = ", ".join(str(index + 1) for index in available[name])
                print(f"Reused {name} repetition {indices}", flush=True)
    return recovered
