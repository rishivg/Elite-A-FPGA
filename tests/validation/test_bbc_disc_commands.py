"""Command checks must observe deadlines, not just eventual held NMI."""

import subprocess
import tempfile
import unittest
from pathlib import Path

from tools.bbc.disc_commands import (
    complete,
    disabled_search_timeout,
    disabled_verify,
    disconnected_counter,
    expected_payload,
    validate_fixture,
)


class DiscCommandTests(unittest.TestCase):
    def test_pre_interrupt_observation_is_required_and_cannot_acknowledge(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "fixture.tsv"
            good = "100 P 0 128 0\n101 R 0 24 1\n"
            path.write_text(good)
            validate_fixture(path, 2, 1)
            for bad in (
                good.replace(" P ", " R "),
                good.replace("128 0", "24 1"),
                good.splitlines()[1] + "\n",
            ):
                path.write_text(bad)
                with self.assertRaises(ValueError):
                    validate_fixture(path, 2, 1)

    def test_complete_and_fault_target_require_actual_observations(self):
        good = subprocess.CompletedProcess(
            [], 0, "138 original bus observations; 0 differences\n", ""
        )
        self.assertTrue(complete(good, 138))
        self.assertFalse(complete(good, 137))
        self.assertFalse(complete(subprocess.CompletedProcess([], 0, good.stdout, "warning"), 138))
        source = "  assign markerIrqs = ((command != 8'h1c) && deletedEligible);\n"
        self.assertEqual(
            disabled_verify(source),
            "  assign markerIrqs = (1'b1 && deletedEligible);\n",
        )
        for bad in (source * 2, "  assign isVerify = (op == 4'b0111);\n"):
            with self.assertRaises(ValueError):
                disabled_verify(bad)

    def test_counter_fault_must_disconnect_exactly_one_host_read(self):
        source = "io_result = eventCounterHigh;\n"
        self.assertEqual(disconnected_counter(source), "io_result = 8'hff;\n")
        for bad in ("", source * 2):
            with self.assertRaises(ValueError):
                disconnected_counter(bad)

    def test_timeout_fault_cuts_both_cell_expiry_and_search_completion(self):
        expiry = "  assign indexExpires = ((((preIndexCallback == 2'b01) && preIndexSpinning) && index) && (preIndexCount == 8'h01));\n"
        callback = "  assign when_Intel8271_l999 = (((preIndexCallback == 2'b01) && preIndexSpinning) && index);\n"
        unrelated = "  assign motorIndex = spinning && index;\n"
        source = expiry + callback + unrelated
        self.assertEqual(
            disabled_search_timeout(source),
            "  assign indexExpires = 1'b0;\n  assign when_Intel8271_l999 = 1'b0;\n" + unrelated,
        )
        for bad in (expiry, callback, expiry + source, callback + source):
            with self.subTest(source=bad), self.assertRaises(ValueError):
                disabled_search_timeout(bad)

    def test_write_requires_complete_payload_addresses_and_deadlines(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "writes.tsv"
            rows = [f"{1000 + 128 * i} {8192 + i} {(19 * i + 3) & 255}\n" for i in range(256)]
            path.write_text("".join(rows))
            self.assertTrue(expected_payload(path, "write"))
            self.assertFalse(expected_payload(path, "read"))
            for bad in (
                rows[:-1],
                [rows[0].replace("8192", "8193"), *rows[1:]],
                [rows[0].replace("1000", "1001"), *rows[1:]],
                [rows[0].replace(" 3\n", " 4\n"), *rows[1:]],
            ):
                path.write_text("".join(bad))
                self.assertFalse(expected_payload(path, "write"))


if __name__ == "__main__":
    unittest.main()
