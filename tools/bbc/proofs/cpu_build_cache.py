"""Discard stale standalone CPU objects before regenerated headers can change ABI."""

import json
import subprocess
from pathlib import Path

from tools.bbc import build
from tools.validation.common import ROOT, digest, write_json

FAMILIES = ("cpu-brk", "cpu-sequences", "cpu-streams", "cpu-stack-streams")
DRIVERS = {
    "cpu-brk": "bbc_cpu_brk_test.o",
    "cpu-sequences": "bbc_cpu_sequences_test.o",
    "cpu-streams": "bbc_cpu_streams_test.o",
    "cpu-stack-streams": "bbc_cpu_streams_test.o",
}


def stale_reasons(directory, driver, versions):
    """A saved binary cannot justify reusing objects compiled with a different ABI."""
    try:
        receipt = json.loads((directory / "build.json").read_text())
    except (OSError, ValueError):
        return ["missing build receipt"]
    inputs = receipt.get("inputs", {})
    reasons = []
    if not inputs or any(
        not (ROOT / name).is_file() or digest(ROOT / name) != sha for name, sha in inputs.items()
    ):
        reasons.append("changed compile inputs")
    if receipt.get("tools") != versions:
        reasons.append("changed compiler or Verilator")
    binary = directory / "VBbcCpu"
    if not binary.is_file() or receipt.get("binary") != digest(binary):
        reasons.append("missing or changed binary")
    header, obj = directory / "VBbcCpu.h", directory / driver
    if header.is_file() and obj.is_file() and obj.stat().st_mtime_ns < header.stat().st_mtime_ns:
        reasons.append("driver object predates generated public header")
    return reasons


def repair(names=FAMILIES):
    """Invalidate whole probe families when their production model needs a rebuild."""
    if set(names) - set(FAMILIES):
        raise ValueError("Unknown standalone CPU probe")
    versions = {
        tool: subprocess.check_output([tool, "--version"], text=True).splitlines()[0]
        for tool in ("c++", "verilator")
    }
    results = []
    for name in names:
        family = build.BBC / name
        directories = sorted(p.parent for p in family.rglob("VBbcCpu.h"))
        actual = family / "actual"
        baseline = stale_reasons(actual, DRIVERS[name], versions) if actual in directories else []
        for directory in directories:
            reasons = baseline or stale_reasons(directory, DRIVERS[name], versions)
            if not reasons:
                continue
            receipt = directory / "build.json"
            saved = json.loads(receipt.read_text()) if receipt.is_file() else None
            removed = []
            for pattern in ("*.o", "*.a"):
                for path in directory.glob(pattern):
                    removed.append(str(path.relative_to(ROOT)))
                    path.unlink()
            receipt.unlink(missing_ok=True)
            results.append(
                {
                    "directory": str(directory.relative_to(ROOT)),
                    "reasons": reasons,
                    "prior_receipt": saved,
                    "removed_rebuildable_objects": removed,
                }
            )
    if results:
        import tempfile

        root = build.BBC / "cpu-object-cache-invalidations"
        root.mkdir(exist_ok=True)
        output = Path(tempfile.mkdtemp(prefix="repair-", dir=root))
        write_json(output / "result.json", {"invalidations": results})
    print(f"Standalone CPU object cache: {len(results)} stale builds invalidated", flush=True)
    return results


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("names", nargs="*", choices=FAMILIES)
    repair(parser.parse_args().names or FAMILIES)
