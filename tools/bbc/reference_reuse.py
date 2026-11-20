"""Reuse sealed original-machine observations; the DUT always executes anew."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from tools.validation.common import ROOT, digest, write_json

from . import boot_cache, storage


def load(path):
    return json.loads(path.read_text())


def file_hashes(directory):
    paths = list(directory.iterdir())
    if any(path.is_symlink() or not path.is_file() for path in paths):
        raise ValueError("Original capture must contain only regular observation files")
    return {path.name: digest(path) for path in sorted(paths)}


def checked_source(source_run, case, config, context=None):
    """Accept a successful frozen case with identical original inputs and own state.

    The source DUT need not have the receiving clock rate or RTL. Its passing
    comparison supplies an as-run seal of every original observation. Neither
    its DUT outputs nor its machine state are copied to the receiving DUT.
    """
    source_run = Path(source_run).resolve()
    if Path(case).name != case or case in {"", ".", ".."}:
        raise ValueError("Original case name must be a single directory name")
    directory = source_run / case
    guards = {}

    def guarded_load(path):
        before = digest(path)
        result = load(path)
        if digest(path) != before:
            raise ValueError("Original receipt changed while reading")
        guards[str(path)] = before
        return result

    source_config = guarded_load(directory / "reference.json")
    # The pinned emulator lives under build/validation/vendor in the frozen root.
    workspace = Path(source_config["jsbeeb"]).parents[3]
    bundle = workspace.parent
    if workspace.name != "workspace" or not source_run.is_relative_to(workspace):
        raise ValueError("Original observations must come from a frozen execution bundle")
    manifest_path = bundle / "execution-bundle.json"
    manifest = guarded_load(manifest_path)
    if not manifest.get("independent_file_copies"):
        raise ValueError("Original execution bundle lacks independent copies")
    # Bind the as-run controller and its comparison/storage seal to the frozen
    # inventory. These host helpers can differ from the receiving implementation.
    controller_names = {
        name
        for name in manifest["files"]
        if name.endswith(".py") and name.startswith(("tools/bbc/", "tools/validation/"))
    }
    if "tools/bbc/run.py" not in controller_names:
        raise ValueError("Original execution bundle lacks its replay controller")
    for name in controller_names:
        path = workspace / name
        value = manifest["files"][name]
        if path.is_symlink() or digest(path) != value:
            raise ValueError("Original replay controller changed: " + name)
        guards[str(path)] = value

    def relative(path):
        path = Path(path)
        if path.is_symlink() or not path.resolve().is_relative_to(workspace):
            raise ValueError("Original producer input escapes its frozen workspace")
        return str(path.relative_to(workspace))

    # Only output roots vary. All declared profiles, schedules and producer
    # paths must agree, including optional bus tracing and audio encoding.
    expected = dict(config)
    actual = dict(source_config)
    expected.pop("output")
    actual.pop("output")
    for key in ("jsbeeb", "cycle_library", "disc", "load_state"):
        if key in actual:
            # Media boot states may reside in the 32 or 96 MHz cache directory;
            # their bytes and independent execution receipt are checked below.
            if key == "load_state":
                actual[key] = digest(Path(actual[key]))
                expected[key] = digest(Path(expected[key]))
                guards[source_config[key]] = actual[key]
                guards[config[key]] = expected[key]
            else:
                actual[key] = relative(actual[key])
                expected[key] = str(Path(expected[key]).relative_to(ROOT))
    if actual != expected or source_config["output"] != str(directory / "reference"):
        raise ValueError("Original capture configuration or own state differs")
    if not boot_cache.valid("reference", context):
        raise ValueError("Receiving original own-boot cache is unverified")
    snapshots_path = source_run / "boot-snapshots.json"
    snapshots = guarded_load(snapshots_path)
    matches = [
        snapshot
        for snapshot in snapshots["media_profiles"].values()
        if snapshot["disc"] == source_config["disc"]
    ]
    if len(matches) != 1:
        raise ValueError("Original capture has no unique independent boot")
    snapshot = matches[0]
    saved = snapshot["independent_boot_receipt"]["backends"]["reference"]
    inputs = boot_cache.inputs("reference", context)
    if not saved.get("independent_execution") or saved.get("inputs") != inputs:
        raise ValueError("Original own-boot inputs or execution provenance differ")
    state = Path(source_config["load_state"])
    if saved.get("states") != {relative(state): digest(state)} or snapshot["hashes"].get(
        str(state)
    ) != digest(state):
        raise ValueError("Original own-boot state changed after capture")

    # Bind actual pinned emulator/dependency bytes to the frozen inventory and
    # the receiving workspace. Host storage helpers may differ; guest producer
    # modules, qualified CPU/ADC libraries and all boot inputs must agree.
    producer_names = set(inputs["files"])
    producer_names.update(
        name for name in manifest["files"] if name.startswith("build/validation/vendor/jsbeeb-")
    )
    vendor_names = {
        name for name in producer_names if name.startswith("build/validation/vendor/jsbeeb-")
    }
    for root in (workspace, ROOT):
        vendor = root / relative(source_config["jsbeeb"])
        actual_names = {
            str(path.relative_to(root))
            for path in vendor.rglob("*")
            if path.is_file() or path.is_symlink()
        }
        if actual_names != vendor_names:
            raise ValueError("Original emulator or dependency inventory changed")
    for name in producer_names:
        source = workspace / name
        receiving = ROOT / name
        expected_hash = inputs["files"].get(name, manifest["files"].get(name))
        if source.is_symlink() or receiving.is_symlink():
            declared_link = manifest.get("symlinks", {}).get(name)
            if (
                declared_link is None
                or not source.is_symlink()
                or not receiving.is_symlink()
                or str(source.readlink()) != declared_link
                or str(receiving.readlink()) != declared_link
                or not receiving.resolve().is_relative_to(ROOT)
            ):
                raise ValueError("Original dependency link changed: " + name)
        # A fresh medium created during the run is in the run input manifest,
        # rather than the initial bundle. Its boot receipt seals its input hash.
        if (
            not source.resolve().is_relative_to(workspace)
            or digest(source) != expected_hash
            or digest(receiving) != expected_hash
        ):
            raise ValueError("Original producer input changed: " + name)
        guards[str(source)] = expected_hash
        guards[str(receiving)] = expected_hash
    comparison_path = directory / "comparison.json"
    comparison = guarded_load(comparison_path)
    capture = directory / "reference"
    observed = file_hashes(capture)
    sealed = {
        name.removeprefix("reference/"): value
        for name, value in comparison.get("artifact_hashes", {}).items()
        if name.startswith("reference/")
    }
    if not comparison.get("passed") or not observed or observed != sealed:
        raise ValueError("Original observations lack an unchanged passing case seal")
    guards.update({str(capture / name): value for name, value in observed.items()})
    summary = guarded_load(capture / "summary.json")
    if summary.get("completed") is not True or summary.get("duration_cycles") != (
        config["scenario"]["duration_us"] * 2
    ):
        raise ValueError("Original capture did not complete the requested schedule")
    if not (capture / storage.MANIFEST).is_file():
        raise ValueError("Original reuse requires verified lossless observation storage")
    for name in guarded_load(capture / storage.MANIFEST)["files"]:
        with storage.open_capture(capture, name):
            pass  # Checks every decoded byte, including otherwise unread tails.
    if any(digest(Path(name)) != value for name, value in guards.items()):
        raise ValueError("Original producer or observations changed during verification")
    return capture, observed, guards


def reuse(source_run, case, config, context=None):
    """Copy only verified reference files and retain their original producer seal."""
    destination = Path(config["output"])
    if destination.exists():
        raise ValueError("Original reuse destination already exists")
    capture, observed, guarded = checked_source(source_run, case, config, context)
    destination.mkdir()
    try:
        for name in observed:
            source, target = capture / name, destination / name
            shutil.copy2(source, target)
            if source.stat().st_ino == target.stat().st_ino and (
                source.stat().st_dev == target.stat().st_dev
            ):
                raise ValueError("Original reuse shares a mutable inode")
        if file_hashes(destination) != observed or any(
            digest(Path(name)) != value for name, value in guarded.items()
        ):
            raise ValueError("Original producer or observations changed during reuse")
        if not boot_cache.valid("reference", context):
            raise ValueError("Receiving original boot changed during reuse")
    except BaseException:
        # A failed copy remains for diagnosis; it cannot receive a reuse seal.
        write_json(destination.parent / "incomplete-reference-reuse.json", {"passed": False})
        raise
    result = {
        "schema": 1,
        "reused": True,
        "source_case": str(capture.parent),
        "source_guards": guarded,
        "observations": observed,
        "scope": "Original observations reused; receiving DUT executes independently",
    }
    write_json(destination.parent / "reference-reuse.json", result)
    return result
