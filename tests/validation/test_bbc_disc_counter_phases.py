"""Counter phase evidence requires all public histories and an actual fault."""

import subprocess
import unittest
from copy import deepcopy

from tools.bbc.disc_counter_phases import CASES, complete, validate_cases, wrong_sync_count


class DiscCounterPhaseTests(unittest.TestCase):
    def test_inventory_rejects_missing_reordered_and_redirected_cases(self):
        good = {
            "own_cold_cases": True,
            "cases": [
                {"name": name, "rows": rows, "fixture": f"original.json.{name}.tsv"}
                for name, rows in CASES
            ],
        }
        self.assertEqual(len(validate_cases(good)), 4)
        for mutate in (
            lambda m: m["cases"].pop(),
            lambda m: m["cases"].reverse(),
            lambda m: m["cases"][0].update(fixture="../other.tsv"),
            lambda m: m.update(own_cold_cases=False),
            lambda m: m["cases"][0].update(rows=True),
        ):
            bad = deepcopy(good)
            mutate(bad)
            with self.assertRaises(ValueError):
                validate_cases(bad)

    def test_complete_requires_exact_full_observations_and_clean_exit(self):
        good = subprocess.CompletedProcess(
            [], 0, "2105 original bus observations; 0 differences\n", ""
        )
        self.assertTrue(complete(good, 2105))
        self.assertFalse(complete(good, 2104))
        self.assertFalse(complete(subprocess.CompletedProcess([], 1, good.stdout, ""), 2105))
        self.assertFalse(complete(subprocess.CompletedProcess([], 0, good.stdout, "warning"), 2105))

    def test_fault_changes_both_live_and_retained_actual_counter_values(self):
        source = "byteCounterLow <= 8'h05;\neventCounterLow = 8'h05;\n"
        self.assertEqual(
            wrong_sync_count(source), "byteCounterLow <= 8'h80;\neventCounterLow = 8'h80;\n"
        )
        for bad in ("", source * 2, source.splitlines()[0]):
            with self.assertRaises(ValueError):
                wrong_sync_count(bad)


if __name__ == "__main__":
    unittest.main()
