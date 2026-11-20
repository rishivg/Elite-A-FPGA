"""Install and verify the independent, pinned released Elite-A reference."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import io
import json
import subprocess
import tarfile
import urllib.request
from functools import lru_cache
from pathlib import Path

from .common import CACHE, LOCK, digest, reference_symbols, source_root, write_json


def disc_files(data: bytes) -> dict[str, bytes]:
    if len(data) < 512 or data[261] % 8:
        raise ValueError("Invalid DFS catalogue")
    files = {}
    for i in range(data[261] // 8):
        entry = data[8 + i * 8 : 16 + i * 8]
        basename = entry[:7].decode("ascii").strip()
        directory = chr(entry[7] & 127)
        name = basename if directory == "$" else f"{directory}.{basename}"
        info = data[264 + i * 8 : 272 + i * 8]
        length = int.from_bytes(info[4:6], "little") + ((info[6] >> 4) & 3) * 65536
        sector = info[7] + (info[6] & 3) * 256
        if sector * 256 + length > len(data) or name in files:
            raise ValueError(f"Invalid disc file: {name}")
        files[name] = data[sector * 256 : sector * 256 + length]
    return files


@lru_cache(maxsize=4)
def _archive_manifest(data: bytes) -> tuple:
    """Decode pinned archive contents once; callers still hash every live file."""
    files = []
    with tarfile.open(fileobj=io.BytesIO(data)) as tar:
        for member in tar:
            if member.isfile():
                relative = Path(*Path(member.name).parts[1:])
                files.append((relative, hashlib.sha256(tar.extractfile(member).read()).hexdigest()))
    return tuple(files)


def verify() -> dict:
    assets = {}
    for key, expected in LOCK["assets"].items():
        source, relative = key.split(":", 1)
        path = source_root(source) / relative
        actual = digest(path)
        if actual != expected:
            raise ValueError(f"Pinned asset checksum mismatch: {key}")
        assets[key] = actual
    # Verify every archived source file as well as the selected ROMs/game image.
    for name, source in LOCK["sources"].items():
        archive = CACHE / "downloads" / f"{name}.tar.gz"
        data = archive.read_bytes()
        if hashlib.sha256(data).hexdigest() != source["archive_sha256"]:
            raise ValueError(f"Archive checksum mismatch: {name}")
        for relative, expected in _archive_manifest(data):
            current = source_root(name) / relative
            if not current.is_file() or digest(current) != expected:
                raise ValueError(f"Reference source modified: {name}/{relative}")
    root = source_root("elite_a")
    files = disc_files((root / "5-compiled-game-discs/elite-a-released.ssd").read_bytes())
    verified = {}
    for name, payload in files.items():
        original = root / "4-reference-binaries/released" / f"{name}.bin"
        if original.exists():
            if payload != original.read_bytes():
                raise ValueError(f"Released disc binary differs from original: {name}")
            verified[name] = hashlib.sha256(payload).hexdigest()
    if len(verified) != LOCK["required_disc_files"]:
        raise ValueError("Released disc verification was incomplete")
    return {"assets": assets, "verified_game_files": verified, "symbols": reference_symbols()}


def dependency_inventory(emulator: Path) -> dict:
    """Hash installed bytes and link targets, including native packages and additions."""
    directory = emulator / "node_modules"
    files = {}
    for path in sorted(directory.rglob("*")):
        name = str(path.relative_to(directory))
        if path.is_symlink():
            if not path.resolve().is_relative_to(directory.resolve()):
                raise ValueError("Reference dependency link escapes node_modules: " + name)
            files[name] = {"link": str(path.readlink())}
        elif path.is_file():
            files[name] = {"sha256": digest(path)}
    if not files:
        raise ValueError("Missing installed reference dependencies")
    return files


def prepare_dependencies(emulator: Path, version: str, *, reinstall=False) -> dict:
    """Serialize initial setup; verified cache hits never replace live packages."""
    receipt = CACHE / "node-dependencies.json"
    inputs = {
        "node": version,
        "package_sha256": digest(emulator / "package.json"),
        "lock_sha256": digest(emulator / "package-lock.json"),
        "recipe": "npm ci --omit=dev --omit=optional --ignore-scripts",
    }
    CACHE.mkdir(parents=True, exist_ok=True)
    with (CACHE / "node-dependencies.lock").open("a+b") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        if receipt.exists() and not reinstall:
            saved = json.loads(receipt.read_text())
            if saved.get("schema") != 1 or saved.get("inputs") != inputs:
                raise ValueError(
                    "Reference dependency setup changed; rerun setup --reinstall-dependencies before starting captures"
                )
            if saved.get("files") != dependency_inventory(emulator):
                raise ValueError(
                    "Reference dependency bytes changed; rerun setup --reinstall-dependencies before starting captures"
                )
            return saved
        subprocess.run(
            [
                "npm",
                "ci",
                "--omit=dev",
                "--omit=optional",
                "--ignore-scripts",
                "--cache",
                str(CACHE / "npm-cache"),
            ],
            cwd=emulator,
            check=True,
            timeout=600,
        )
        saved = {"schema": 1, "inputs": inputs, "files": dependency_inventory(emulator)}
        write_json(receipt, saved)
        return saved


def prepare(*, reinstall_dependencies=False) -> None:
    for name, source in LOCK["sources"].items():
        path = CACHE / "downloads" / f"{name}.tar.gz"
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            url = f"https://codeload.github.com/{source['repository']}/tar.gz/{source['commit']}"
            print(f"Downloading pinned {name}", flush=True)
            # Default TLS verification remains enabled.
            with urllib.request.urlopen(url, timeout=60) as response:
                data = response.read()
            if hashlib.sha256(data).hexdigest() != source["archive_sha256"]:
                raise ValueError(f"Downloaded archive checksum mismatch: {name}")
            path.write_bytes(data)
        if digest(path) != source["archive_sha256"]:
            raise ValueError(f"Archive checksum mismatch: {name}")
        if not source_root(name).exists():
            (CACHE / "vendor").mkdir(parents=True, exist_ok=True)
            with tarfile.open(path) as tar:
                tar.extractall(CACHE / "vendor", filter="data")
    metadata = verify()
    emulator = source_root("jsbeeb")
    version = subprocess.run(
        ["node", "--version"], capture_output=True, text=True, check=True
    ).stdout.strip()
    if tuple(map(int, version.lstrip("v").split("."))) < (24, 15, 0):
        raise ValueError(f"Pinned emulator requires Node >=24.15.0; found {version}")
    # Cache hits hash every dependency byte instead of deleting packages under
    # concurrent readers. Explicit repair is performed before captures start.
    prepare_dependencies(emulator, version, reinstall=reinstall_dependencies)
    write_json(CACHE / "reference-verification.json", metadata)
    print(
        f"Verified {len(metadata['verified_game_files'])} released game files, pinned ROMs, and emulator sources"
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify-only", action="store_true")
    parser.add_argument(
        "--reinstall-dependencies",
        action="store_true",
        help="Repair dependency drift from the pinned lock before starting captures",
    )
    args = parser.parse_args()
    if args.verify_only:
        metadata = verify()
        print(f"Verified {len(metadata['verified_game_files'])} released game binaries")
    else:
        prepare(reinstall_dependencies=args.reinstall_dependencies)


if __name__ == "__main__":
    main()
