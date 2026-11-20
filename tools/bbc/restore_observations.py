"""Restore archived observations as independent ordinary files with their recorded hashes."""

import argparse
import json
import re
import shutil
from pathlib import Path, PurePosixPath

from tools.validation.common import ROOT, digest


def restore(inventory, prefix=None):
    inventory = Path(inventory).resolve()
    manifest = json.loads(inventory.read_text())
    if manifest.get("schema") != 1 or not manifest.get("members"):
        raise ValueError("Unsupported observation archive")
    selected = []
    verified = set()
    for name, item in manifest["members"].items():
        path = PurePosixPath(name)
        if path.is_absolute() or ".." in path.parts or path.parts[:2] != ("build", "bbc"):
            raise ValueError("Observation path escapes the BBC artifact tree")
        if prefix is not None and not name.startswith(prefix.rstrip("/") + "/"):
            continue
        sha = item.get("sha256", "")
        if (
            not isinstance(sha, str)
            or re.fullmatch(r"[0-9a-f]{64}", sha) is None
            or item.get("blob") != sha + ".blob"
            or type(item.get("bytes")) is not int
            or item["bytes"] < 0
            or type(item.get("mode")) is not int
            or not 0 <= item["mode"] <= 0o777
        ):
            raise ValueError("Invalid archived observation identity")
        blob = inventory.parent / item["blob"]
        if blob.resolve().parent != inventory.parent:
            raise ValueError("Observation blob escapes its archive")
        if (sha, item["bytes"]) not in verified:
            if blob.stat().st_size != item["bytes"] or digest(blob) != sha:
                raise ValueError("Archived observation is damaged")
            verified.add((sha, item["bytes"]))
        destination = ROOT / name
        if not destination.resolve().is_relative_to(ROOT / "build/bbc"):
            raise ValueError("Observation destination escapes the BBC artifact tree")
        if destination.exists() and digest(destination) != sha:
            raise ValueError("Existing observation differs; refusing to overwrite it")
        selected.append((blob, destination, item))
    if not selected:
        raise ValueError("No observations match the requested prefix")
    for blob, destination, item in selected:
        if destination.exists():
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        # Each restored capture gets its own inode. Shared hardlinks would
        # invalidate the independent-copy checks used by reference reuse.
        shutil.copyfile(blob, destination)
        destination.chmod(item["mode"])
        if digest(destination) != item["sha256"]:
            raise ValueError("Restored observation differs")
    return len(selected)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inventory", type=Path)
    parser.add_argument("--prefix", help="Restore only one recorded build/bbc capture directory")
    args = parser.parse_args()
    print(f"Verified {restore(args.inventory, args.prefix)} restored observations")
