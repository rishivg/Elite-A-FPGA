"""Load checksum-pinned preservation inputs without depending on Git history."""

import hashlib
import json
from pathlib import Path

from tools.validation.common import ROOT

DIRECTORY = ROOT / "tests/fixtures/rtl-baselines"


def historical(commit: str, path: str) -> str:
    manifest = json.loads((DIRECTORY / "manifest.json").read_text())
    try:
        expected = manifest[commit][path]
    except KeyError as error:
        raise ValueError(f"Unknown proof baseline: {commit}:{path}") from error
    data = (DIRECTORY / commit / path).read_bytes()
    if hashlib.sha256(data).hexdigest() != expected:
        raise ValueError(f"Proof baseline checksum mismatch: {commit}:{path}")
    return data.decode("utf-8")


def dependencies(commit: str) -> list[Path]:
    manifest = json.loads((DIRECTORY / "manifest.json").read_text())
    return [
        Path(__file__),
        DIRECTORY / "manifest.json",
        *[DIRECTORY / commit / path for path in manifest[commit]],
    ]
