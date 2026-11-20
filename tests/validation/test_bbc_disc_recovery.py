"""Recovery receipts must retain every independent history and observation."""

import copy
import subprocess
import unittest

from tools.bbc.disc_recovery import CASES, case_name, complete, held_head, validate_cases


def metadata():
    return {
        "own_cold_cases": True,
        "rows": len(CASES) * 145,
        "cases": [
            {
                "target": target,
                "select": select,
                "elapsed": elapsed,
                "read_track": read_track,
                "name": case_name(select, target, elapsed, read_track),
                "fixture": f"original.json.{case_name(select, target, elapsed, read_track)}.tsv",
                "rows": 145,
            }
            for select, target, elapsed, read_track in CASES
        ],
    }


class DiscRecoveryReceiptTests(unittest.TestCase):
    def test_complete_independent_inventory_required(self):
        good = metadata()
        self.assertEqual(len(validate_cases(good)), 27)
        for field, value in (("own_cold_cases", False), ("rows", 1), ("cases", good["cases"][:-1])):
            with self.assertRaisesRegex(ValueError, "Incomplete"):
                validate_cases({**good, field: value})
        for field, value in (
            ("rows", True),
            ("rows", 144),
            ("fixture", "other.tsv"),
            ("name", "other"),
            ("elapsed", 21),
        ):
            bad = copy.deepcopy(good)
            bad["cases"][0][field] = value
            with self.assertRaisesRegex(ValueError, "Incomplete"):
                validate_cases(bad)

    def test_observations_must_be_complete_and_exact(self):
        good = "145 original bus observations; 0 differences\n"
        self.assertTrue(complete(subprocess.CompletedProcess([], 0, good, ""), 145))
        for code, output, error in (
            (1, good, ""),
            (0, good, "warning"),
            (0, good.replace("145", "144"), ""),
            (0, good.replace("0 differences", "1 differences"), ""),
        ):
            self.assertFalse(complete(subprocess.CompletedProcess([], code, output, error), 145))

    def test_fault_requires_all_four_actual_step_assignments(self):
        step = "physicalTrack <= (physicalTrack + 8'h01);\n"
        self.assertEqual(held_head(step * 8).count("physicalTrack <= physicalTrack;"), 8)
        with self.assertRaisesRegex(ValueError, "actual physical-head"):
            held_head(step * 7)


if __name__ == "__main__":
    unittest.main()
