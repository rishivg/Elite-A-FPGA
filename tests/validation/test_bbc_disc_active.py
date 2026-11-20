"""Reject incomplete independent active-command histories and absent fault targets."""

import unittest
from copy import deepcopy

from tools.bbc.disc_active import early_operation_change, expected_names, validate_cases


class DiscActiveTests(unittest.TestCase):
    def test_inventory_requires_complete_transfers_and_independent_fixtures(self):
        good = {
            "own_cold_cases": True,
            "cases": [
                {
                    "name": name,
                    "rows": 100,
                    "fixture": f"original.json.{name}.tsv",
                    "storageWrites": [],
                    "complete": name.endswith("-1"),
                    "bytes": 256,
                    "result": 0,
                }
                for name in expected_names()
            ],
        }
        self.assertEqual(len(validate_cases(good)), 16)
        completion = next(i for i, c in enumerate(good["cases"]) if c["complete"])
        for mutate in (
            lambda m: m["cases"].pop(),
            lambda m: m["cases"].reverse(),
            lambda m: m["cases"][0].update(rows=True),
            lambda m: m["cases"][0].update(fixture="../other.tsv"),
            lambda m: m["cases"][completion].update(bytes=255),
            lambda m: m["cases"][completion].update(result=10),
            lambda m: m.update(own_cold_cases=False),
        ):
            bad = deepcopy(good)
            mutate(bad)
            with self.assertRaises(ValueError):
                validate_cases(bad)

    def test_fault_changes_real_retained_operation_once(self):
        target = "assign activeOp = (physicalOperation ? executingOp : op);"
        self.assertEqual(early_operation_change(target), "assign activeOp = op;")
        for bad in ("", target + target):
            with self.assertRaises(ValueError):
                early_operation_change(bad)
