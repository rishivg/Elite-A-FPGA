"""Reject incomplete or malformed independently generated timer histories."""

import tempfile
import unittest
from pathlib import Path

from tools.bbc.via_timer_history import validate_fixture


class TimerHistoryFixtureTests(unittest.TestCase):
    def check(self, text):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "reference.tsv"
            path.write_text(text)
            return validate_fixture(path)

    def fixture(self):
        row = "0 0 13 0 0 0 0 1\n"
        return "".join(
            f"case {index}\n" + row * count for index, count in enumerate([519] * 128 + [17] * 768)
        )

    def test_complete_fixture_has_all_cases_and_operations(self):
        self.assertEqual(self.check(self.fixture()), {"cases": 896, "operations": 79488})

    def test_matching_prefix_does_not_admit_a_missing_tail(self):
        with self.assertRaisesRegex(ValueError, "Incomplete"):
            self.check(self.fixture().rsplit("\n", 2)[0] + "\n")

    def test_missing_case_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Missing"):
            self.check(self.fixture().replace("case 895\n", "case 896\n"))

    def test_irq_must_match_the_reference_flags(self):
        with self.assertRaisesRegex(ValueError, "Invalid"):
            self.check(self.fixture().replace("0 0 13 0 0 0 0 1", "0 0 13 0 0 128 0 1", 1))

    def test_malformed_bus_observations_are_rejected(self):
        for row in ("", "0 0 2 0 0 0 0 1", "36 0 13 0 0 0 0 1", "0 0 13 0 0 0 0 2"):
            with self.subTest(row=row), self.assertRaises(ValueError):
                self.check(self.fixture().replace("0 0 13 0 0 0 0 1", row, 1))


if __name__ == "__main__":
    unittest.main()
