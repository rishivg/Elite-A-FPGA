"""Detect missing, reordered and differently timed accepted bus transactions."""

import tempfile
import unittest
from pathlib import Path

from tools.bbc.boot_trace import compare_events, dut_events, reference_events


class BootTraceTests(unittest.TestCase):
    def test_missing_tail_is_a_failure(self):
        report = compare_events(iter([(16, 3, 4), (32, 5, 6)]), iter([(16, 3, 4)]))
        self.assertFalse(report["passed"])
        self.assertEqual(report["reference_events"], 2)
        self.assertEqual(report["dut_events"], 1)
        self.assertEqual(report["first_difference"]["reference"], (32, 5, 6))
        self.assertIsNone(report["first_difference"]["dut"])

    def test_wrong_clock_and_empty_traces_are_failures(self):
        report = compare_events(iter([(16, 3, 4)]), iter([(17, 3, 4)]))
        self.assertFalse(report["passed"])
        self.assertEqual(report["different_events"], 1)
        self.assertFalse(compare_events(iter([]), iter([]))["passed"])

    def test_select_video_transactions_without_reordering(self):
        with tempfile.TemporaryDirectory() as directory:
            reference, dut = Path(directory) / "reference", Path(directory) / "dut"
            reference.write_text(
                "16 ula 0 0\n2 ram 1 2\n3 crtc 65024 3\n4 scroll 0 0\n5 ula 65057 4\n"
            )
            dut.write_text("48 65024 3 0 0\n64 65089 8 0 0\n80 65057 4 0 0\n")
            report = compare_events(
                reference_events(reference, {"crtc", "ula"}), dut_events(dut, video=True)
            )
            self.assertTrue(report["passed"])
            self.assertEqual(report["dut_events"], 2)

    def test_unexpected_non_bus_video_write_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "reference"
            path.write_text("17 ula 0 0\n")
            with self.assertRaises(ValueError):
                list(reference_events(path, {"ula"}))


if __name__ == "__main__":
    unittest.main()
