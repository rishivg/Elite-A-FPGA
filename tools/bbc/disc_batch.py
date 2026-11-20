"""Replay FDC histories with optional process-local, independently executed DUT prefixes."""

import re
import subprocess

from tools.validation.common import ROOT

from . import build, disc_commands

DRIVER = ROOT / "sim/native/bbc_disc_batch_test.cpp"
HEADER = ROOT / "sim/native/bbc_own_prefix.h"


def run(directory, cases, *, cache=True):
    names = [case["name"] for case in cases]
    if (
        not names
        or len(set(names)) != len(names)
        or any(re.fullmatch(r"[A-Za-z0-9_.-]+", name) is None for name in names)
    ):
        raise ValueError("Missing, duplicate or invalid FDC history name")
    for case in cases:
        disc_commands.validate_fixture(directory / case["fixture"], case["rows"], 0)
    manifest = directory / "manifest.tsv"
    manifest.write_text(
        "".join(f"{directory / case['fixture']} {case['name']}\n" for case in cases)
    )
    # A failed batch must not report successful files left by an earlier run.
    for name in names:
        for suffix in (".log", ".writes.tsv", ".raw.tsv"):
            (directory / (name + suffix)).unlink(missing_ok=True)
    batch = subprocess.run(
        [
            str(directory / "VIntel8271"),
            str(manifest),
            str(build.BBC / "disc.ssd"),
            str(directory),
            "1" if cache else "0",
        ],
        cwd=build.BBC,
        capture_output=True,
        text=True,
        timeout=900,
    )
    batch_log = directory / "batch.log"
    batch_log.write_text(batch.stdout + batch.stderr)
    results = []
    artifacts = [manifest, batch_log]
    for case in cases:
        writes = directory / (case["name"] + ".writes.tsv")
        log = directory / (case["name"] + ".log")
        result = subprocess.CompletedProcess(
            batch.args,
            batch.returncode,
            log.read_text() if log.is_file() else "",
            batch.stderr,
        )
        results.append((case, result, writes))
        artifacts.extend(
            [directory / case["fixture"], log, writes, directory / (case["name"] + ".raw.tsv")]
        )
    reuse = {
        "enabled": cache,
        "source": "Current process's own successfully executed DUT prefixes without storage writes",
        "execution": batch.stdout.strip(),
        "returncode": batch.returncode,
    }
    return results, artifacts, reuse
