"""Run the comparison from a frozen copy of its inputs."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from tools.bbc import adc_results, boot_cache, build, cycle_reference, profile
from tools.validation.common import LOCK, ROOT, digest, source_root, write_json
from tools.validation.setup import verify


def receipt_paths(value):
    """Collect real file keys from nested content receipts, never command text."""
    result = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if isinstance(key, str) and not Path(key).is_absolute():
                candidate = ROOT / key
                if candidate.is_file() and candidate.resolve().is_relative_to(ROOT):
                    result.add(candidate)
            result.update(receipt_paths(item))
    elif isinstance(value, list):
        for item in value:
            result.update(receipt_paths(item))
    return result


def execution_files(clock_multiplier=1, optimized96=False):
    verify()
    boot_cache.configure(clock_multiplier=clock_multiplier)
    if optimized96:
        from . import profile96

        if clock_multiplier != 3 or not profile96.current():
            raise ValueError("A verified current 96 MHz profile is required before snapshotting")
    if (
        not build.native_current(clock_multiplier)
        or not cycle_reference.current()
        or not adc_results.current()
    ):
        raise ValueError("Build or reference is stale; prepare and qualify it before snapshotting")
    tracked = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT).split(b"\0")
    files = {ROOT / os.fsdecode(name) for name in tracked if name}
    bbc = build.BBC
    if clock_multiplier == 1:
        native_receipt = build.load_receipt("native-build.json")
        receipts = [bbc / "elaboration.json", bbc / "native-build.json"]
    else:
        native_receipt = json.loads((bbc / "full-video/native/build.json").read_text())
        receipts = [
            bbc / "elaboration.json",
            bbc / "full-video/elaboration.json",
            bbc / "full-video/native/build.json",
        ]
    if boot_cache.receipt_path().exists():
        receipts.append(boot_cache.receipt_path())
    receipts += [adc_results.RECEIPT, cycle_reference.RECEIPT]
    if native_receipt.get("optimization"):
        receipts.append(profile.RECEIPT)
    if optimized96:
        receipts.append(profile96.RECEIPT)
    files.update(receipts)
    for receipt in receipts:
        files.update(receipt_paths(json.loads(receipt.read_text())))
    files.update(
        [
            build.native_binary(clock_multiplier),
            adc_results.BINARY,
            adc_results.CORPUS,
            cycle_reference.BINARY,
        ]
    )
    files.update(path for path in boot_cache.STATES if path.is_file())
    # Include runtime files that have not been added to Git yet.
    files.update(
        path
        for path in (ROOT / "tools").rglob("*")
        if path.is_file() and path.suffix in {".py", ".mjs", ".json"}
    )
    files.update(
        path
        for path in (ROOT / "sim/native").iterdir()
        if path.is_file() and path.suffix in {".c", ".cpp", ".h", ".v"}
    )
    files.update(
        path
        for path in (ROOT / "rtl").rglob("*")
        if path.is_file() and path.suffix in {".scala", ".v"}
    )
    files.update(bbc.glob("Bbc*.v_*.bin"))
    files.update(
        bbc / name
        for name in [
            "BbcMachine.v",
            "BbcSystemTop.v",
            "ArletCpuEnabled.v",
            "ArletCpuEvents.v",
            "BbcCpuEvents.v",
            "boot.keys",
            "rom.hex",
            "disc.ssd",
        ]
    )
    directories = [
        bbc / ("boots" if clock_multiplier == 1 else "full-video/boots"),
        bbc / "media",
        adc_results.DIRECTORY / "sources",
    ]
    for name in LOCK["sources"]:
        files.add(ROOT / "build/validation/downloads" / (name + ".tar.gz"))
        directories.append(source_root(name))
    files.add(ROOT / "build/validation/reference-verification.json")
    dependency_receipt = ROOT / "build/validation/node-dependencies.json"
    if dependency_receipt.is_file():
        files.add(dependency_receipt)
    if native_receipt.get("optimization"):
        saved = native_receipt["optimization"]
        files.add(profile.NATIVE / "VBbcMachine")
        files.update(profile.DATA.glob("*.gcda"))
        for case in saved["verification"]:
            for model in ["baseline", "optimized"]:
                directories.append(profile.DIRECTORY / case["name"] / model / "capture")
    if optimized96:
        files.add(profile96.BINARY)
        files.update(profile96.DATA.glob("*.gcda"))
        for case in json.loads(profile96.RECEIPT.read_text())["verification"]:
            for model in ("baseline", "optimized"):
                directories.append(profile96.DIRECTORY / case["name"] / model / "capture")
    for directory in directories:
        if directory.exists():
            files.update(path for path in directory.rglob("*") if path.is_file())
    if any(not path.is_file() or not path.resolve().is_relative_to(ROOT) for path in files):
        raise ValueError("Execution bundle contains a missing file or an external symlink")
    return sorted(files)


def copy_bundle(files, destination):
    """Use independent files; hard links could silently follow later edits."""
    before = {str(path.relative_to(ROOT)): digest(path) for path in files}
    links = {str(path.relative_to(ROOT)): path.readlink() for path in files if path.is_symlink()}
    if any(target.is_absolute() for target in links.values()):
        raise ValueError("Execution bundle cannot retain an absolute source link")
    for path in files:
        copied = destination / path.relative_to(ROOT)
        copied.parent.mkdir(parents=True, exist_ok=True)
        if path.is_symlink():
            if not path.resolve().is_relative_to(ROOT):
                raise ValueError("Execution copy contains an external source link")
            copied.symlink_to(links[str(path.relative_to(ROOT))])
        else:
            shutil.copy2(path, copied)
            if os.path.samestat(path.stat(), copied.stat()):
                raise ValueError("Execution copy shares an inode with the working tree")
    after = {str(path.relative_to(ROOT)): digest(path) for path in files}
    actual = {name: digest(destination / name) for name in before}
    after_links = {
        str(path.relative_to(ROOT)): path.readlink() for path in files if path.is_symlink()
    }
    copied_links = {
        name: (destination / name).readlink()
        for name in before
        if (destination / name).is_symlink()
    }
    if before != after or before != actual or links != after_links or links != copied_links:
        raise ValueError("Execution files or links changed while creating the bundle")
    return before


def main():
    arguments = sys.argv[1:]
    if arguments[:1] == ["--"]:
        arguments = arguments[1:]
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--clock-multiplier", type=int, choices=[1, 3], default=1)
    parser.add_argument("--profile96", action="store_true")
    parser.add_argument("--prefer-profile96", action="store_true")
    parser.add_argument("--reference-run", type=Path)
    parser.add_argument("--reuse-pairs-from", type=Path)
    parser.add_argument("--reuse-boots-from", type=Path)
    options, _ = parser.parse_known_args(arguments)
    for i, argument in enumerate(arguments):
        flag, equals, value = argument.partition("=")
        if flag in {"--reference-run", "--reuse-pairs-from", "--reuse-boots-from"}:
            if equals:
                arguments[i] = f"{flag}={Path(value).resolve()}"
            else:
                arguments[i + 1] = str(Path(arguments[i + 1]).resolve())
    if options.prefer_profile96:
        from . import profile96

        if options.clock_multiplier != 3:
            parser.error("--prefer-profile96 requires --clock-multiplier 3")
        arguments = [argument for argument in arguments if argument != "--prefer-profile96"]
        if not options.profile96 and profile96.faster():
            arguments.append("--profile96")
            options.profile96 = True
    files = execution_files(options.clock_multiplier, options.profile96)
    required = sum(path.stat().st_size for path in files)
    reserve = 128 * 1024 * 1024
    if shutil.disk_usage(build.BBC).free < required + reserve:
        raise ValueError(
            f"Execution copy needs {required} bytes plus {reserve} bytes of capture headroom; "
            "free space before retrying. Longer replays need additional headroom."
        )
    directory = Path(tempfile.mkdtemp(prefix="execution-", dir=build.BBC))
    working = directory / "workspace"
    try:
        inputs = copy_bundle(files, working)
    except Exception as error:
        # Discard the incomplete copy and record why it failed.
        if working.exists():
            shutil.rmtree(working)
        write_json(directory / "INCOMPLETE.json", {"passed": False, "error": str(error)})
        raise
    recipe = {
        "source_root": str(ROOT),
        "git_head": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip(),
        "files": inputs,
        "bytes": sum(path.stat().st_size for path in files),
        "independent_file_copies": True,
        "symlinks": {
            str(path.relative_to(ROOT)): str(path.readlink()) for path in files if path.is_symlink()
        },
        "helper_sha256": digest(Path(__file__)),
        "scope": "Exact runtime, generated RTL, pinned sources/dependencies, qualified binaries and each machine's own existing boot. No cross-machine restore or game-state injection.",
    }
    write_json(directory / "execution-bundle.json", recipe)
    env = dict(os.environ)
    env["PYTHONPATH"] = str(working)
    check = f"from tools.bbc import build,adc_results,cycle_reference; from tools.validation.setup import verify; verify(); assert build.native_current({options.clock_multiplier}) and adc_results.current() and cycle_reference.current(); print('Copied assets and native/reference receipts verified; normal boot guards select or independently execute own boots', flush=True)"
    if options.profile96:
        check += "; from tools.bbc import profile96; assert profile96.current()"
    subprocess.run([sys.executable, "-c", check], cwd=working, env=env, check=True)
    print("Isolated execution bundle:", directory, flush=True)
    log_path = directory / "execution.log"
    with log_path.open("w") as log:
        result = subprocess.run(
            [sys.executable, "-m", "tools.bbc.run", *arguments],
            cwd=working,
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
    write_json(
        directory / "execution-result.json",
        {
            "exit_code": result.returncode,
            "bundle_manifest": digest(directory / "execution-bundle.json"),
            "arguments": arguments,
            "log": log_path.name,
            "scope": "Use the copied runtime's report and unchanged-input/own-boot guards; command exit 1 can still report remaining equivalence gaps.",
        },
    )
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
