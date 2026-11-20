"""Last-sector validation must reject partial transfers and hidden overflows."""

import copy
import subprocess
import tempfile
import unittest
from pathlib import Path

from tools.bbc.disc_bounds import complete, payload_matches, unbounded_address, validate_cases


class DiscBoundsTests(unittest.TestCase):
    def test_requires_both_full_independent_histories(self):
        good = {
            "own_cold_cases": True,
            "cases": [
                {
                    "track": t,
                    "sector": 9,
                    "written": 256,
                    "readback": 256,
                    "rows": 1061,
                    "fixture": f"original.json.track{t}.tsv",
                }
                for t in (76, 79)
            ],
        }
        self.assertEqual(len(validate_cases(good)), 2)
        for field, value in (("readback", 255), ("rows", 1060), ("fixture", "other.tsv")):
            bad = copy.deepcopy(good)
            bad["cases"][-1][field] = value
            with self.assertRaises(ValueError):
                validate_cases(bad)
        with self.assertRaises(ValueError):
            validate_cases({**good, "own_cold_cases": False})
        with self.assertRaises(ValueError):
            validate_cases({**good, "cases": good["cases"][:1]})

    def test_payload_requires_exact_addresses_values_and_spacing(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "writes.tsv"
            rows = [[100 + 128 * i, 204544 + i, (19 * i + 3) & 255] for i in range(256)]

            def write(values):
                path.write_text("".join(" ".join(map(str, row)) + "\n" for row in values))

            write(rows)
            self.assertTrue(payload_matches(path, 79))
            write(rows[:-1])
            self.assertFalse(payload_matches(path, 79))
            for column in range(3):
                bad = copy.deepcopy(rows)
                bad[128][column] += 1
                write(bad)
                self.assertFalse(payload_matches(path, 79))

    def test_completion_and_fault_target_are_strict(self):
        good = subprocess.CompletedProcess(
            [], 0, "1061 original bus observations; 0 differences\n", ""
        )
        self.assertTrue(complete(good, 1061))
        self.assertFalse(complete(good, 1060))
        self.assertFalse(complete(subprocess.CompletedProcess([], 0, good.stdout, "warning"), 1061))
        source = "  wire [17:0] requestedStorageAddress;\n  assign io_storageAddress = bound ? requestedStorageAddress : 0;\n"
        self.assertIn(
            "assign io_storageAddress = requestedStorageAddress;", unbounded_address(source)
        )
        with self.assertRaises(ValueError):
            unbounded_address(source.replace("requestedStorageAddress", "missing"))


if __name__ == "__main__":
    unittest.main()
