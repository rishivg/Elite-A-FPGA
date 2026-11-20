"""Reuse sealed Yosys preparation while always running the requested SAT check."""

import json
import shutil
import subprocess
from pathlib import Path

from tools.bbc.proofs import memory_composition
from tools.bbc.proofs.video_cursor import hashes
from tools.validation.common import digest, write_json


def key(command, files):
    return {"command": command, "files": hashes([*files, Path(shutil.which("yosys"))])}


def prepare(directory, command, files, outputs, timeout=1200):
    receipt, log = directory / "prepared.json", directory / "prepare.log"
    expected = key(command, files)
    cached = json.loads(receipt.read_text()) if receipt.exists() else {}
    origin = cached.get("origin")
    origin_valid = origin is None or (
        Path(origin["receipt"]).is_file() and digest(Path(origin["receipt"])) == origin["sha256"]
    )
    if (
        origin_valid
        and cached.get("inputs") == expected
        and all(p.is_file() for p in [log, *outputs])
        and cached.get("outputs") == hashes([log, *outputs])
    ):
        return True
    with log.open("w") as stream:
        subprocess.run(
            ["yosys", "-Q", "-T", "-p", command],
            stdout=stream,
            stderr=subprocess.STDOUT,
            check=True,
            timeout=timeout,
        )
    write_json(receipt, {"inputs": expected, "outputs": hashes([log, *outputs])})
    return False


def seed(directory, command, files, outputs, passed_receipt, preparation_log):
    """Import preparation only from a fully sealed successful proof."""
    saved = memory_composition.sealed(passed_receipt)
    bound = {**saved["inputs"], **saved["artifacts"]}
    for name, expected in hashes(
        [*files, *outputs, preparation_log, Path(shutil.which("yosys"))]
    ).items():
        if bound.get(name) != expected:
            raise ValueError("Unbound cache seed input/artifact: " + name)
    origin = directory / "preparation-origin.json"
    shutil.copyfile(passed_receipt, origin)
    log = directory / "prepare.log"
    shutil.copyfile(preparation_log, log)
    write_json(
        directory / "prepared.json",
        {
            "inputs": key(command, files),
            "outputs": hashes([log, *outputs]),
            "origin": {"receipt": str(origin), "sha256": digest(origin)},
        },
    )
