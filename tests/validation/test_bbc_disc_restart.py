"""Incomplete independent restart observations must fail qualification."""

import copy
import subprocess
import unittest

from tools.bbc.disc_restart import CASES, complete, validate_cases


def metadata():
    return {
        "own_cold_cases": True,
        "rows": len(CASES) * 20,
        "cases": [
            {"name": name, "rows": 20, "fixture": f"original.json.{name}.bus.tsv"} for name in CASES
        ],
    }


class DiscRestartReceiptTests(unittest.TestCase):
    def test_every_declared_case_is_required_once(self):
        expected = metadata()
        self.assertEqual(len(validate_cases(expected)), len(CASES))
        for invalid in (expected["cases"][:-1], expected["cases"] + expected["cases"][:1]):
            with self.assertRaisesRegex(ValueError, "incomplete"):
                validate_cases({**expected, "cases": invalid})

    def test_counts_cold_initialization_and_fixture_identity_are_required(self):
        for field, value in (("own_cold_cases", False), ("rows", 1)):
            with self.assertRaisesRegex(ValueError, "incomplete"):
                validate_cases({**metadata(), field: value})
        for field, value in (("rows", 19), ("rows", True), ("fixture", "other.bus.tsv")):
            invalid = copy.deepcopy(metadata())
            invalid["cases"][0][field] = value
            with self.assertRaisesRegex(ValueError, "incomplete"):
                validate_cases(invalid)

    def test_exact_complete_native_observation_count_is_required(self):
        good = "20 original restart observations; 0 differences; 1 restarts\n"
        result = subprocess.CompletedProcess([], 0, good, "")
        self.assertTrue(complete(result, 20))
        for output in (
            good.replace("20", "19"),
            good.replace("0 differences", "1 differences"),
            good.replace("1 restarts", "0 restarts"),
            good + good,
        ):
            self.assertFalse(complete(subprocess.CompletedProcess([], 0, output, ""), 20))
        self.assertFalse(complete(subprocess.CompletedProcess([], 1, good, ""), 20))
        self.assertFalse(complete(subprocess.CompletedProcess([], 0, good, "warning"), 20))


if __name__ == "__main__":
    unittest.main()
