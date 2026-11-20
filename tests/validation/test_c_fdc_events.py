"""Device event evidence rejects incomplete traces and vacuous fault outcomes."""

import subprocess
import unittest

from tools.bbc.proofs.cpu_reference import fdc_events


class FdcEventTests(unittest.TestCase):
    def test_fixture_inventory_rejects_duplicates_truncation_and_unordered_rows(self):
        parts = [
            f"case {i} {name} 2\n17 W 0 53 0 0 0\n18 P 0 0 0 0 0\n"
            for i, name in enumerate(fdc_events.NAMES)
        ]
        source = "".join(parts)
        self.assertEqual(list(fdc_events.fixtures(source)), fdc_events.NAMES)
        for bad in (
            "".join(parts[:-1]),
            source[:-2],
            source + parts[0],
            source.replace("18 P", "17 P", 1),
            source.replace("17 W 0", "17 W 8", 1),
            source.replace("case 1 ", "case 0 ", 1),
        ):
            with self.subTest(source=bad), self.assertRaises(ValueError):
                fdc_events.fixtures(bad)

    def test_negative_controls_require_the_named_observational_difference(self):
        row = "FDC_EPOCH_MISMATCH read-abort cycle 1206177 return 28/28 nmi 1/1 edges 1/2\n"
        result = subprocess.CompletedProcess([], 1, row, "")
        self.assertTrue(fdc_events.negative_result(result, "read-abort"))
        for code, output, error in (
            (0, row, ""),
            (2, row, ""),
            (1, row, "timeout"),
            (1, row.replace("edges 1/2", "edges 0/2"), ""),
            (1, row.replace("read-abort", "other"), ""),
            (1, row.replace("edges 1/2", "edges 2/2"), ""),
            (1, "Missing fixture or SSD\n", ""),
        ):
            with self.subTest(output=output):
                self.assertFalse(
                    fdc_events.negative_result(
                        subprocess.CompletedProcess([], code, output, error), "read-abort"
                    )
                )
        for name in ("head-reset--1", "track-retry"):
            result.stdout = row.replace("read-abort", name)
            self.assertTrue(fdc_events.negative_result(result, name))
            result.stdout = result.stdout.replace("edges 1/2", "edges 2/2")
            self.assertFalse(fdc_events.negative_result(result, name))


if __name__ == "__main__":
    unittest.main()
