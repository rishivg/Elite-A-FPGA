"""Detect incomplete or mistimed serial endpoint observations."""

import tempfile
import unittest
from pathlib import Path

from tools.bbc.via_cpu_endpoint import compare_traces, with_fetches


class SerialEndpointTraceTests(unittest.TestCase):
    def compare(self, original, actual, rows=2, cases=1):
        with tempfile.TemporaryDirectory() as temporary:
            files = [Path(temporary) / name for name in ("original", "dut")]
            for path, content in zip(files, (original, actual)):
                path.write_text(content)
            return compare_traces(*files, rows, cases)

    def test_interrupt_observations_do_not_replace_missing_bus_rows(self):
        trace = "0 R RES 0\n0 I D 84\n"
        report = self.compare(trace, trace)
        self.assertFalse(report["passed"])
        self.assertEqual(report["non_interrupt_observations"], 1)

    def test_timestamp_differences_are_failures_even_with_matching_flags(self):
        original = "0 R RES 0\n0 I D 84\n2 W CB1 1\n"
        actual = "0 R RES 0\n1 I D 84\n1 W CB1 1\n"
        report = self.compare(original, actual)
        self.assertFalse(report["passed"])
        self.assertEqual(report["differences"], 2)

    def test_missing_tail_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Incomplete"):
            self.compare("0 R RES 0\n2 W CB1 1\n", "0 R RES 0\n")

    def test_fetches_release_selection_after_register_accesses(self):
        self.assertEqual(
            list(with_fetches(["0 R RES 0", "1 W A 5A", "0 R CB1 1", "1 R D 00"])),
            ["0 R RES 0", "1 W A 5A", "2 W CB1 0", "0 R CB1 1", "1 R D 00", "2 W CB1 0"],
        )


if __name__ == "__main__":
    unittest.main()
