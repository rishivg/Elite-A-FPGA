"""Retain a completed isolated run's unchanged, independently executed own boots."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import shutil
import tempfile
from contextlib import contextmanager
from pathlib import Path

from tools.validation.common import ROOT, digest, write_json

from . import boot_cache, build


def load(path):
    return json.loads(path.read_text())


def destination_snapshot(paths):
    if any(path.is_symlink() or (path.exists() and not path.is_file()) for path in paths):
        raise ValueError("Receiving own-boot path must be a regular file")
    return {str(path.relative_to(ROOT)): digest(path) if path.is_file() else None for path in paths}


def copy_boot_file(source, destination, expected):
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    if digest(destination) != expected:
        raise ValueError(f"Own-boot copy differs from its receipt: {destination}")


@contextmanager
def install_boot_files(staging, targets, receipt, previous):
    """Publish staged states and restore the old cache if sealing fails."""
    try:
        for target in targets:
            target.parent.mkdir(parents=True, exist_ok=True)
            os.replace(staging / target.relative_to(ROOT), target)
        yield
    except BaseException as failure:
        for path in [receipt, *targets]:
            try:
                backup = previous / path.relative_to(ROOT)
                if backup.is_file():
                    shutil.copy2(backup, path)
                else:
                    path.unlink(missing_ok=True)
            except OSError as error:
                failure.add_note(f"Could not restore {path}: {error}")
        raise


def checked_states(workspace, backend, saved, context=None):
    inputs = boot_cache.inputs(backend) if context is None else boot_cache.inputs(backend, context)
    if not saved.get("independent_execution") or saved.get("inputs") != inputs:
        raise ValueError(f"{backend} own-boot inputs changed or independent execution is absent")
    names = [str(path.relative_to(ROOT)) for path in boot_cache.backends(context)[backend]]
    if set(saved.get("states", {})) != set(names):
        raise ValueError(f"{backend} own-boot files do not match the selected profile")
    for name in names:
        path = workspace / name
        if not path.is_file() or path.is_symlink() or not path.resolve().is_relative_to(workspace):
            raise ValueError("Missing or linked own-boot file: " + name)
        if digest(path) != saved["states"][name]:
            raise ValueError("Own-boot bytes changed: " + name)
    return names


def reuse(
    bundle,
    *,
    sealed_boots_only=False,
    run=None,
    clock_multiplier=1,
    context=None,
    backend=None,
    reference_options=None,
):
    """Import completed own boots with unchanged inputs; never convert model state.

    Run after captures finish in the receiving workspace. A valid existing boot
    is left in place. Earlier destination bytes and their receipt are retained
    before publishing newly verified files.
    """
    bundle = Path(bundle).resolve()
    requested_backend = backend
    options = (
        {key: reference_options[key] for key in boot_cache.REFERENCE_OPTIONS}
        if reference_options is not None
        else {}
    )
    boot_cache.configure(clock_multiplier=clock_multiplier, **options)
    if context is not None and context.clock_multiplier != clock_multiplier:
        raise ValueError("Own-boot media profile has a different master clock")
    backends = boot_cache.backends(context)
    if backend is not None:
        if backend not in backends:
            raise ValueError("Unknown own-boot backend")
        backends = {backend: backends[backend]}
    workspace = bundle / "workspace"
    if workspace == ROOT or not workspace.is_dir():
        raise ValueError("Expected an isolated execution bundle with its workspace intact")
    result_path = bundle / "execution-result.json"
    manifest_path = bundle / "execution-bundle.json"
    receipt_path = workspace / boot_cache.receipt_path(context).relative_to(ROOT)
    manifest = load(manifest_path)
    if not manifest.get("independent_file_copies"):
        raise ValueError("Isolated execution is incomplete or its manifest changed")
    if not sealed_boots_only:
        result = load(result_path)
        if result.get("bundle_manifest") != digest(manifest_path) or result.get(
            "exit_code"
        ) not in {0, 1}:
            raise ValueError("Isolated execution is incomplete or its manifest changed")
    receipt = load(receipt_path)
    if receipt.get("schema") != 2:
        raise ValueError("Unrecognized own-boot receipt")
    latest = workspace / (run if run is not None else "build/bbc/latest")
    if not latest.resolve().is_relative_to(workspace):
        raise ValueError("Run report points outside its isolated workspace")
    report_path = latest / "report.json"
    snapshots_path = latest / "boot-snapshots.json"
    if not sealed_boots_only:
        report = load(report_path)
        if not report.get("inputs_unchanged") or not report.get("boot_provenance_complete"):
            raise ValueError(
                "Completed replay did not verify unchanged inputs and own-boot provenance"
            )
    snapshots = load(snapshots_path)
    if context is not None:
        source_disc = str(workspace / context.disc.relative_to(ROOT))
        matches = [
            snapshot
            for snapshot in snapshots.get("media_profiles", {}).values()
            if snapshot.get("disc") == source_disc
        ]
        if len(matches) != 1:
            raise ValueError("No unique own-boot snapshot for the selected medium")
        snapshots = matches[0]
    if snapshots.get("independent_boot_receipt") != receipt:
        raise ValueError("Own-boot receipt changed after the replay")
    if not build.native_current(clock_multiplier):
        raise ValueError("Receiving native implementation is stale")
    guarded = [manifest_path, receipt_path, snapshots_path]
    if not sealed_boots_only:
        guarded += [result_path, report_path]
    names = []
    for backend in backends:
        names += checked_states(
            workspace, backend, receipt.get("backends", {}).get(backend, {}), context
        )
    observed = snapshots.get("hashes", {})
    expected = {str(workspace / name): digest(workspace / name) for name in names}
    if (requested_backend is None and observed != expected) or any(
        observed.get(name) != value for name, value in expected.items()
    ):
        raise ValueError("Replayed own-boot state bytes changed")
    guarded += [workspace / name for name in names]
    original = (
        build.hashes(guarded)
        if workspace.is_relative_to(ROOT)
        else {str(path): digest(path) for path in guarded}
    )

    def check_source():
        actual = (
            build.hashes(guarded)
            if workspace.is_relative_to(ROOT)
            else {str(path): digest(path) for path in guarded}
        )
        if actual != original or not build.native_current(clock_multiplier):
            raise ValueError("Boot source or receiving native implementation changed during reuse")
        for backend in backends:
            checked_states(workspace, backend, receipt["backends"][backend], context)

    build.BBC.mkdir(parents=True, exist_ok=True)
    with (build.BBC / "boot-reuse.lock").open("a+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        destination = boot_cache.receipt_path(context)
        destination_snapshot(
            [destination, *[path for states in backends.values() for path in states]]
        )
        pending = [backend for backend in backends if not boot_cache.valid(backend, context)]
        if not pending:
            check_source()
            return {"reused": [], "existing_boots_current": True}
        try:
            earlier = load(destination)
        except (OSError, ValueError):
            earlier = {"schema": 2, "backends": {}}
        if earlier.get("schema") != 2 or not isinstance(earlier.get("backends"), dict):
            earlier = {"schema": 2, "backends": {}}
        targets = [path for backend in pending for path in backends[backend]]
        watched = [destination, *targets]
        before = destination_snapshot(watched)
        source_states = {
            name: value
            for backend in pending
            for name, value in receipt["backends"][backend]["states"].items()
        }
        with tempfile.TemporaryDirectory(prefix="boot-reuse-", dir=build.BBC) as temporary:
            staging = Path(temporary)
            for target in targets:
                relative = target.relative_to(ROOT)
                copy_boot_file(workspace / relative, staging / relative, source_states[str(relative)])
            check_source()
            if before != destination_snapshot(watched):
                raise ValueError("Receiving boot changed while staging reuse")
            history = Path(tempfile.mkdtemp(prefix="retained-", dir=build.BBC))
            for path in watched:
                relative = path.relative_to(ROOT)
                if before[str(relative)] is not None:
                    copy_boot_file(path, history / relative, before[str(relative)])
            write_json(history / "receipt.json", {"files": before, "source_bundle": str(bundle)})
            check_source()
            if before != destination_snapshot(watched):
                raise ValueError("Receiving boot changed before publishing reuse")
            with install_boot_files(staging, targets, destination, history):
                for backend in pending:
                    earlier["backends"][backend] = receipt["backends"][backend]
                write_json(destination, earlier)
                check_source()
                if not all(boot_cache.valid(name, context) for name in backends):
                    raise ValueError("Reused own-boot receipt is not current")
        return {
            "reused": pending,
            "states": {backend: boot_cache.state_hashes(backend, context) for backend in pending},
            "source_bundle": str(bundle),
            "sealed_boots_only": sealed_boots_only,
            "clock_multiplier": clock_multiplier,
            "source_guards": original,
            "retained_previous": str(history),
            "scope": "Same backend, exact boot inputs and profile; original independently executed state bytes, without state injection or clock changes",
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", type=Path)
    parser.add_argument("--clock-multiplier", type=int, choices=[1, 3], default=1)
    parser.add_argument("--backend", choices=["dut", "reference"])
    parser.add_argument(
        "--run",
        type=Path,
        help="Run path within the private workspace; defaults to completed latest",
    )
    parser.add_argument(
        "--sealed-boots-only",
        action="store_true",
        help="Retain already sealed own boots while the private replay continues; no replay completion claim",
    )
    arguments = parser.parse_args()
    result = reuse(
        arguments.bundle,
        sealed_boots_only=arguments.sealed_boots_only,
        run=arguments.run,
        clock_multiplier=arguments.clock_multiplier,
        backend=arguments.backend,
    )
    write_json(build.BBC / "boot-reuse.json", result)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
