"""Execute a small batch of unittest modules, with a receipt after each module."""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import time
import unittest
from pathlib import Path


@contextlib.contextmanager
def module_output(stream):
    """Capture Python and inherited native stdout/stderr in the same module log."""
    descriptors = [(fd, os.dup(fd)) for fd in (1, 2)]
    try:
        for fd, _ in descriptors:
            os.dup2(stream.fileno(), fd)
        with contextlib.redirect_stdout(stream), contextlib.redirect_stderr(stream):
            yield
    finally:
        stream.flush()
        for fd, saved in descriptors:
            os.dup2(saved, fd)
            os.close(saved)


def execute(directory: Path, modules: list[str], output: Path):
    """Keep module fixtures and outcomes separate while amortizing Python imports."""
    output.mkdir(parents=True, exist_ok=True)
    for name in modules:
        started = time.monotonic()
        log = output / (Path(name).stem + ".log")
        with log.open("w") as stream, module_output(stream):
            # A new loader and runner preserve unittest's module/class fixture
            # boundaries. A failing module does not suppress its batch peers.
            suite = unittest.TestLoader().discover(str(directory), pattern=name)
            result = unittest.TextTestRunner(stream=stream, verbosity=1).run(suite)
        report = {
            "module": name,
            "tests": result.testsRun,
            "passed": result.wasSuccessful() and result.testsRun > 0,
            "exit_status": 0 if result.wasSuccessful() and result.testsRun > 0 else 1,
            "seconds": time.monotonic() - started,
            "log": str(log),
            "skipped": len(result.skipped),
            "expected failures": len(result.expectedFailures),
            "unexpected successes": len(result.unexpectedSuccesses),
            "failures": len(result.failures),
            "errors": len(result.errors),
        }
        # Completed peers remain reviewable even if the next module crashes
        # the process. A partial receipt cannot look like a completed module.
        receipt = output / (Path(name).stem + ".json")
        temporary = receipt.with_suffix(".tmp")
        temporary.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
        temporary.replace(receipt)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("modules", nargs="+")
    args = parser.parse_args(argv)
    execute(args.directory, args.modules, args.output)


if __name__ == "__main__":
    main()
