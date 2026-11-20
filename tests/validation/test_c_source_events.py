"""Event-capture controls must expose the behavioral failure, not a setup error."""

import json
import subprocess
import unittest

from tools.bbc.proofs.cpu_reference.source_events import outcome


class SourceEventTests(unittest.TestCase):
    PREFIX = "elite.ssd loaded as 80 track: its catalogue claims 800 sectors\n"

    def test_only_precise_missing_second_handler_satisfies_negative_control(self):
        error = (
            "AssertionError [ERR_ASSERTION]: ABORT_RETRIGGER_LOST\n  actual: 1,\n  expected: 2,\n"
        )
        trial = subprocess.CompletedProcess([], 1, self.PREFIX, error)
        self.assertTrue(outcome(trial, "abort", False))
        for code, output, failure in (
            (0, self.PREFIX, error),
            (1, "", error),
            (1, self.PREFIX, "Module not found"),
            (1, self.PREFIX, error.replace("actual: 1", "actual: 0")),
            (1, self.PREFIX, error.replace("actual: 1", "actual: 10")),
            (1, self.PREFIX, error.replace("expected: 2", "expected: 3")),
            (1, self.PREFIX, error.replace("expected: 2", "expected: 20")),
            (1, self.PREFIX + "unexpected output", error),
        ):
            with self.subTest(code=code, output=output, failure=failure):
                self.assertFalse(
                    outcome(subprocess.CompletedProcess([], code, output, failure), "abort", False)
                )

    def test_all_closed_domain_cases_and_native_lifecycle_are_required(self):
        for mode, result in (
            ("domain", {"passed": True, "cases": 96}),
            (
                "lifecycle",
                {"passed": True, "restored_levels": 2, "restored_pending": True, "handlers": 9},
            ),
        ):
            trial = subprocess.CompletedProcess([], 0, json.dumps(result), "")
            self.assertTrue(outcome(trial, mode, True))
            for bad in (
                {},
                [],
                "not json",
                {**result, "passed": False},
                {**result, "cases": 95, "restored_levels": 1},
            ):
                with self.subTest(mode=mode, result=bad):
                    trial.stdout = json.dumps(bad)
                    self.assertFalse(outcome(trial, mode, True))


if __name__ == "__main__":
    unittest.main()
