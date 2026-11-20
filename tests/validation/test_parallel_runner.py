"""Parallel test execution must preserve counts and propagate child failures."""

import tempfile
import unittest
from pathlib import Path

from tools.validation.test import NATIVE_MODULES, run_tests


class ParallelRunnerTests(unittest.TestCase):
    def test_child_failure_retains_successful_peer_and_all_test_counts(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            tests = root / "tests"
            tests.mkdir()
            for name, condition in [("good", "True"), ("bad", "False")]:
                (tests / f"test_{name}.py").write_text(
                    "import unittest\nclass Child(unittest.TestCase):\n"
                    f" def test_result(self): self.assertTrue({condition})\n"
                )
            report = run_tests(tests, "test_*.py", 5, root / "logs")
            self.assertFalse(report["passed"])
            self.assertEqual(report["tests"], 2)
            self.assertEqual(report["jobs"], 5)
            self.assertEqual(
                {r["module"]: r["passed"] for r in report["modules"]},
                {"test_bad.py": False, "test_good.py": True},
            )
            self.assertTrue(all(Path(r["log"]).is_file() for r in report["modules"]))

    def test_empty_module_is_a_failure(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            tests = root / "tests"
            tests.mkdir()
            (tests / "test_empty.py").write_text("")
            report = run_tests(tests, "test_*.py", 1, root / "logs")
            self.assertFalse(report["passed"])
            self.assertEqual(report["tests"], 0)

    def test_unit_and_native_selections_partition_the_complete_suite(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            tests = root / "tests"
            tests.mkdir()
            native = sorted(NATIVE_MODULES)[0]
            for name in ("test_unit.py", native):
                (tests / name).write_text(
                    "import unittest\nclass Child(unittest.TestCase):\n"
                    " def test_result(self): self.assertTrue(True)\n"
                )
            unit = run_tests(tests, "test_*.py", 1, root / "unit", suite="unit")
            probe = run_tests(tests, "test_*.py", 1, root / "native", suite="native")
            complete = run_tests(tests, "test_*.py", 1, root / "all")
            self.assertTrue(all(r["passed"] for r in (unit, probe, complete)))
            self.assertEqual(unit["selection"]["excluded_modules"], [native])
            self.assertEqual(probe["selection"]["excluded_modules"], ["test_unit.py"])
            self.assertEqual(unit["tests"] + probe["tests"], complete["tests"])
            self.assertEqual(complete["selection"]["excluded_modules"], [])

    def test_first_run_starts_long_native_probe_before_short_unit_module(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            tests, order = root / "tests", root / "order.txt"
            tests.mkdir()
            for name in ("test_aaa.py", "test_bbc_disc_oracle.py"):
                (tests / name).write_text(
                    "import unittest\n"
                    f"with open({str(order)!r}, 'a') as stream: stream.write({name!r} + chr(10))\n"
                    "class Child(unittest.TestCase):\n"
                    " def test_result(self): self.assertTrue(True)\n"
                )
            report = run_tests(tests, "test_*.py", 1, root / "logs")
            self.assertTrue(report["passed"])
            self.assertEqual(
                order.read_text().splitlines(), ["test_bbc_disc_oracle.py", "test_aaa.py"]
            )

    def test_batches_keep_fixtures_outcomes_and_logs_per_module(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            tests, events = root / "tests", root / "events.txt"
            tests.mkdir()
            for name in ("bad", "good", "skipped", "expected", "unexpected"):
                decorators = {
                    "skipped": "@unittest.skip('fixture skip')",
                    "expected": "@unittest.expectedFailure",
                    "unexpected": "@unittest.expectedFailure",
                }
                (tests / f"test_{name}.py").write_text(
                    "import unittest\n"
                    f"def record(value):\n with open({str(events)!r}, 'a') as stream: stream.write(value + chr(10))\n"
                    f"def setUpModule(): record('{name}:setup')\n"
                    f"def tearDownModule(): record('{name}:teardown')\n"
                    "class Child(unittest.TestCase):\n"
                    f" {decorators.get(name, '')}\n"
                    f" def test_result(self): self.assertTrue({name not in ('bad', 'expected')})\n"
                )
            report = run_tests(tests, "test_*.py", 1, root / "logs", batch_size=8)
            self.assertFalse(report["passed"])
            self.assertEqual(report["tests"], 5)
            self.assertEqual(report["worker_processes"], 1)
            outcomes = {r["module"]: r for r in report["modules"]}
            self.assertTrue(outcomes["test_good.py"]["passed"])
            self.assertEqual(outcomes["test_skipped.py"]["skipped"], 1)
            self.assertEqual(outcomes["test_expected.py"]["expected failures"], 1)
            self.assertEqual(outcomes["test_unexpected.py"]["unexpected successes"], 1)
            self.assertEqual(
                events.read_text().splitlines(),
                [
                    f"{name}:{event}"
                    for name in sorted(("bad", "good", "skipped", "expected", "unexpected"))
                    for event in ("setup", "teardown")
                ],
            )

    def test_worker_crash_cannot_reuse_previous_success_or_drop_later_modules(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            tests = root / "tests"
            tests.mkdir()
            (tests / "test_a.py").write_text(
                "import unittest\nclass Child(unittest.TestCase):\n def test_ok(self): pass\n"
            )
            (tests / "test_b.py").write_text("import os\nos._exit(7)\n")
            (tests / "test_c.py").write_text(
                "import unittest\nclass Child(unittest.TestCase):\n def test_ok(self): pass\n"
            )
            report = run_tests(tests, "test_*.py", 1, root / "logs")
            self.assertFalse(report["passed"])
            self.assertEqual(report["tests"], 1)
            self.assertEqual(len(report["modules"]), 3)
            self.assertTrue(report["modules"][0]["passed"])
            self.assertFalse(report["modules"][1]["passed"])
            self.assertFalse(report["modules"][2]["passed"])
            self.assertEqual(report["worker_errors"][0]["exit_status"], 7)
            with self.assertRaisesRegex(ValueError, "fresh directory"):
                run_tests(tests, "test_*.py", 1, root / "logs")

    def test_native_modules_are_never_batched(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            tests = root / "tests"
            tests.mkdir()
            names = ["test_a.py", "test_b.py", sorted(NATIVE_MODULES)[0]]
            for name in names:
                (tests / name).write_text(
                    "import unittest\nclass Child(unittest.TestCase):\n def test_ok(self): pass\n"
                )
            report = run_tests(tests, "test_*.py", 1, root / "logs")
            self.assertTrue(report["passed"])
            self.assertEqual(report["worker_processes"], 2)

    def test_extended_native_parallelism_is_rejected_before_execution(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            tests = root / "tests"
            tests.mkdir()
            (tests / "test_psg_sweep.py").write_text("raise RuntimeError('must not import')\n")
            with self.assertRaisesRegex(ValueError, "share build outputs"):
                run_tests(tests, "test_*.py", 2, root / "logs", suite="extended")
            self.assertFalse((root / "logs").exists())

    def test_native_stdout_and_stderr_stay_in_the_module_log(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            tests = root / "tests"
            tests.mkdir()
            (tests / "test_output.py").write_text(
                "import os, subprocess, sys, unittest\n"
                "class Child(unittest.TestCase):\n"
                " def test_output(self):\n"
                "  print('python-output')\n"
                "  os.write(1, b'descriptor-output\\n')\n"
                "  subprocess.run([sys.executable, '-c', \"import sys; print('native-output'); print('native-error', file=sys.stderr)\"], check=True)\n"
            )
            report = run_tests(tests, "test_*.py", 1, root / "logs")
            self.assertTrue(report["passed"])
            log = Path(report["modules"][0]["log"]).read_text()
            for expected in ("python-output", "descriptor-output", "native-output", "native-error"):
                self.assertIn(expected, log)
