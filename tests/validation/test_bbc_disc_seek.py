"""Mechanical seek validation must keep every independent history."""

import copy
import subprocess
import unittest

from tools.bbc.disc_seek import CASES, complete, held_head_current, validate_cases


class DiscSeekTests(unittest.TestCase):
    def test_inventory_requires_complete_motor_recovery_and_track_readbacks(self):
        cases = [
            {
                "name": n,
                "tracks": t,
                "bad": b,
                "mode": m,
                "rows": r,
                "unload": u,
                "fixture": f"original.json.{n}.tsv",
                "observations": [{"track": v} for v in t],
            }
            for n, t, b, m, r, u in CASES
        ]
        good = {"own_cold_cases": True, "cases": cases}
        self.assertEqual(len(validate_cases(good)), 5)
        for value in (cases[:-1], cases[::-1]):
            with self.assertRaises(ValueError):
                validate_cases({**good, "cases": value})
        for field, value in (("rows", 1726), ("unload", False), ("observations", [])):
            bad = copy.deepcopy(good)
            bad["cases"][-1][field] = value
            with self.assertRaises(ValueError):
                validate_cases(bad)

    def test_outputs_and_actual_fault_target_are_required(self):
        run = subprocess.CompletedProcess(
            [], 0, "1727 original bus observations; 0 differences\n", ""
        )
        self.assertTrue(complete(run, 1727))
        self.assertFalse(complete(run, 1726))
        self.assertFalse(complete(subprocess.CompletedProcess([], 1, run.stdout, ""), 1727))
        source = "driveOut[4] <= (8'h2b <= targetTrack);"
        self.assertEqual(held_head_current(source), "driveOut[4] <= 1'b0;")
        with self.assertRaises(ValueError):
            held_head_current(source * 2)


if __name__ == "__main__":
    unittest.main()
