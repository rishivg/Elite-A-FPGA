"""Run independent unittest modules in bounded processes, retaining every test."""

from __future__ import annotations

import argparse
import json
import math
import os
import resource
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from tools.bbc.host_budget import capacity

from .common import ROOT, write_json

NATIVE_MODULES = frozenset(
    {
        "test_bbc_adc.py",
        "test_bbc_audio.py",
        "test_bbc_disc_oracle.py",
        "test_bbc_reference.py",
        "test_bbc_reset_prefix.py",
        "test_bbc_system.py",
        "test_bbc_via.py",
    }
)


# These probes share generated machine workspaces and must run serially.
EXTENDED_NATIVE_MODULES = frozenset(
    {"test_key_irq.py", "test_loading_video.py", "test_psg_sweep.py"}
)


# Fallback durations for scheduling the first run.
INITIAL_SECONDS = {
    "test_bbc_disc_oracle.py": 120,
    "test_bbc_audio.py": 20,
    "test_bbc_via.py": 20,
    "test_bbc_system.py": 15,
    "test_bbc_adc.py": 15,
    "test_bbc_reset_prefix.py": 6,
    "test_bbc_reference.py": 5,
}


def batches_for(modules, jobs, batch_size, history, suite):
    isolated = NATIVE_MODULES | (EXTENDED_NATIVE_MODULES if suite == "extended" else frozenset())
    batches = [[module] for module in modules if module.name in isolated]
    short = [module for module in modules if module.name not in isolated]
    count = max(jobs, math.ceil(len(short) / batch_size)) if short else 0
    groups = [[] for _ in range(min(count, len(short)))]
    weights = [0.0] * len(groups)
    for module in short:
        eligible = [i for i, group in enumerate(groups) if len(group) < batch_size]
        index = min(eligible, key=lambda i: (weights[i], len(groups[i]), i))
        groups[index].append(module)
        weights[index] += history.get(module.name, INITIAL_SECONDS.get(module.name, 0))
    batches.extend(groups)
    batches.sort(
        key=lambda group: -sum(history.get(p.name, INITIAL_SECONDS.get(p.name, 0)) for p in group)
    )

    return batches


def execute_batch(batch, directory, output, env):
    begin = time.monotonic()
    child = subprocess.run(
        [
            sys.executable,
            "-m",
            "tools.validation.test_worker",
            "--directory",
            str(directory.resolve()),
            "--output",
            str(output.resolve()),
            *(module.name for module in batch),
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    results = []
    for module in batch:
        receipt = output / (module.stem + ".json")
        if receipt.exists():
            result = json.loads(receipt.read_text())
        else:
            log = output / (module.stem + ".log")
            with log.open("a") as stream:
                stream.write(child.stdout + child.stderr)
                stream.write("\nWorker stopped without a completed module receipt.\n")
            result = {
                "module": module.name,
                "tests": 0,
                "passed": False,
                "exit_status": child.returncode or 1,
                "seconds": 0,
                "log": str(log),
                "skipped": 0,
                "expected failures": 0,
                "unexpected successes": 0,
                "failures": 0,
                "errors": 1,
            }
        results.append(result)
    return results, time.monotonic() - begin, child.returncode


def run_tests(
    directory: Path,
    pattern: str,
    jobs: int,
    output: Path,
    history=None,
    *,
    suite="all",
    batch_size=8,
):
    if suite not in {"all", "unit", "native", "extended"}:
        raise ValueError("Unknown test suite")
    if type(jobs) is not int or jobs < 1:
        raise ValueError("Select a positive number of test workers")
    if type(batch_size) is not int or not 1 <= batch_size <= 32:
        raise ValueError("Select a batch size between one and 32")
    matching = sorted(directory.glob(pattern))
    modules = [
        p
        for p in matching
        if suite in {"all", "extended"} or (p.name in NATIVE_MODULES) == (suite == "native")
    ]
    if not modules:
        raise ValueError("No test modules selected")
    if (
        suite == "extended"
        and jobs != 1
        and any(p.name in EXTENDED_NATIVE_MODULES for p in modules)
    ):
        raise ValueError(
            "Extended native tests share build outputs; use --jobs 1 or select --pattern test_c_*.py"
        )
    output.mkdir(parents=True, exist_ok=True)
    if any((output / (p.stem + suffix)).exists() for p in modules for suffix in (".json", ".log")):
        raise ValueError(
            "Test output already contains selected module artifacts; use a fresh directory"
        )
    history = history or {}
    modules.sort(key=lambda p: (-history.get(p.name, INITIAL_SECONDS.get(p.name, 0)), p.name))
    # Give each worker one compiler job to avoid oversubscription.
    env = {**os.environ, "VERILATOR_JOBS": "1"}
    started = time.monotonic()
    cpu_start = resource.getrusage(resource.RUSAGE_CHILDREN)

    batches = batches_for(modules, jobs, batch_size, history, suite)

    results, worker_errors, batch_seconds = [], [], []
    with ThreadPoolExecutor(max_workers=jobs) as executor:
        pending = {
            executor.submit(execute_batch, batch, directory, output, env): batch
            for batch in batches
        }
        for future in as_completed(pending):
            completed, seconds, status = future.result()
            results.extend(completed)
            batch_seconds.append(seconds)
            if status:
                worker_errors.append(
                    {"modules": [p.name for p in pending[future]], "exit_status": status}
                )
            for result in completed:
                print(
                    f"{'PASS' if result['passed'] else 'FAIL'} {result['module']}: {result['tests']} tests, {result['seconds']:.2f}s",
                    flush=True,
                )
                if not result["passed"]:
                    print(Path(result["log"]).read_text(), flush=True)
    cpu_end = resource.getrusage(resource.RUSAGE_CHILDREN)
    return {
        "child_cpu_seconds": cpu_end.ru_utime
        + cpu_end.ru_stime
        - cpu_start.ru_utime
        - cpu_start.ru_stime,
        "passed": not worker_errors and all(r["passed"] for r in results),
        "tests": sum(r["tests"] for r in results),
        "skipped": sum(r["skipped"] for r in results),
        "seconds": time.monotonic() - started,
        "jobs": jobs,
        "batch_size": batch_size,
        "worker_processes": len(batches),
        "worker_seconds": sum(batch_seconds),
        "worker_errors": worker_errors,
        "selection": {
            "suite": suite,
            "pattern": pattern,
            "selected_modules": sorted(p.name for p in modules),
            "excluded_modules": sorted(p.name for p in matching if p not in modules),
        },
        "modules": sorted(results, key=lambda r: r["module"]),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--jobs", type=int, default=None)
    parser.add_argument("--pattern", default="test_*.py")
    parser.add_argument("--suite", choices=("all", "unit", "native", "extended"), default="all")
    parser.add_argument(
        "--batch-size",
        type=int,
        choices=range(1, 33),
        default=8,
        help="Python modules per worker; 1 restores module process isolation",
    )
    args = parser.parse_args(argv)
    if args.jobs is not None and args.jobs < 1:
        parser.error("--jobs must be positive")
    jobs = args.jobs if args.jobs is not None else (1 if args.suite == "extended" else capacity())
    base = ROOT / "build/validation/tests"
    base.mkdir(parents=True, exist_ok=True)
    history_file = base / ("extended-timings.json" if args.suite == "extended" else "timings.json")
    try:
        history = json.loads(history_file.read_text())
    except (OSError, ValueError):
        history = {}
    if not isinstance(history, dict):
        history = {}
    history = {
        name: seconds
        for name, seconds in history.items()
        if isinstance(seconds, (int, float)) and math.isfinite(seconds) and seconds >= 0
    }
    output = Path(tempfile.mkdtemp(prefix="run-", dir=base))
    report = run_tests(
        ROOT / ("tests/bbc_extended" if args.suite == "extended" else "tests/validation"),
        args.pattern,
        jobs,
        output,
        history,
        suite=args.suite,
        batch_size=args.batch_size,
    )
    write_json(output / "report.json", report)
    write_json(history_file, {**history, **{r["module"]: r["seconds"] for r in report["modules"]}})
    print(
        f"{report['tests']} tests in {report['seconds']:.2f}s; report: {output}/report.json",
        flush=True,
    )
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
