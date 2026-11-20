"""Reject unsafe boundaries and stale evidence before gameplay checkpoint reuse."""

import copy
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.bbc.continuation import equal_tail, load_prefix, recipe, split
from tools.validation.common import digest, write_json


class ContinuationTests(unittest.TestCase):
    def setUp(self):
        self.scenario = {
            "name": "route",
            "duration_us": 200000,
            "events": [
                {"at_us": 100, "keys": ["F3"]},
                {"at_us": 200, "keys": []},
                {"at_us": 120000, "keys": ["F8"]},
                {"at_us": 130000, "keys": []},
            ],
            "analogue": [],
            "checkpoints": [
                {"at_us": 90000, "name": "menu"},
                {"at_us": 180000, "name": "status"},
            ],
            "reference_assertions": [
                {"checkpoint": "menu", "field": "credits_tenths", "equals": 5000},
                {"checkpoint": "status", "field": "docked", "equals": True},
            ],
        }

    def test_partition_preserves_physical_history_and_outcomes(self):
        original = copy.deepcopy(self.scenario)
        prefix, suffix = split(self.scenario, 100000)
        self.assertEqual(self.scenario, original)
        self.assertEqual(prefix["events"], original["events"][:2])
        self.assertEqual(suffix["events"][0], {"at_us": 20000, "keys": ["F8"]})
        self.assertEqual(suffix["checkpoints"], [{"at_us": 80000, "name": "status"}])
        self.assertEqual(prefix["reference_assertions"], original["reference_assertions"][:1])
        self.assertEqual(suffix["reference_assertions"], original["reference_assertions"][1:])

    def test_held_key_or_non_neutral_analogue_boundary_fails(self):
        self.scenario["events"][1]["keys"] = ["F3"]
        with self.assertRaisesRegex(ValueError, "Release every"):
            split(self.scenario, 100000)
        self.scenario["events"][1]["keys"] = []
        for channels, buttons in (([0, 32768, 32768, 32768], 0), ([32768] * 4, 1)):
            self.scenario["analogue"] = [{"at_us": 0, "channels": channels, "buttons": buttons}]
            with self.assertRaisesRegex(ValueError, "Centre"):
                split(self.scenario, 100000)

    def test_missing_observation_or_outside_boundary_fails(self):
        for at in (0, 200000, -1, True):
            with self.assertRaises(ValueError):
                split(self.scenario, at)
        with self.assertRaisesRegex(ValueError, "Both parts"):
            split(self.scenario, 50000)

    def test_streamed_tail_checks_lengths_and_late_corruption(self):
        with tempfile.TemporaryDirectory() as tmp:
            a, b = Path(tmp) / "full", Path(tmp) / "tail"
            data = bytes(range(256)) * 8193
            a.write_bytes(b"prefix" + data)
            b.write_bytes(data)
            self.assertTrue(equal_tail(a, b, 6))
            b.write_bytes(data[:-1])
            self.assertFalse(equal_tail(a, b, 6))
            b.write_bytes(data[:-1] + b"x")
            self.assertFalse(equal_tail(a, b, 6))

    def receipt(self, directory):
        prefix = split(self.scenario, 100000)[0]
        names = {
            "prefix-1/dut/own.state",
            "prefix-1/dut/own.state.ssd",
            "prefix-1/dut/own.state.rgb",
            "prefix-1/reference/own.state",
            "prefix-2/dut/own.state",
            "prefix-2/reference/own.state",
            *[
                f"{p}-{n}/comparison.json"
                for p in ("prefix", "continuous", "suffix")
                for n in (1, 2)
            ],
        }
        for name in names:
            path = directory / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"frozen evidence")
        receipt = {
            "schema": 1,
            "prefix_recipe": recipe(prefix),
            "provenance": {"observer": "current"},
            "passed": True,
            "repeatable": True,
            "continuous_equivalence": True,
            "sealed_files": {name: digest(directory / name) for name in names},
        }
        path = directory / "report.json"
        write_json(path, receipt)
        return path, prefix, receipt

    def test_reuse_rejects_stale_source_recipe_state_or_missing_evidence(self):
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch("tools.bbc.continuation.provenance", return_value={"observer": "current"}),
        ):
            directory = Path(tmp)
            path, prefix, receipt = self.receipt(directory)
            self.assertEqual(load_prefix(path, prefix)["dut"], directory / "prefix-1/dut/own.state")
            renamed = copy.deepcopy(prefix)
            renamed["name"] = "another-branch-prefix"
            renamed["notes"] = ["Different description; physical prefix and observations identical"]
            self.assertEqual(load_prefix(path, renamed), load_prefix(path, prefix))
            changed = copy.deepcopy(prefix)
            changed["events"][1]["at_us"] += 1
            with self.assertRaisesRegex(ValueError, "incompatible"):
                load_prefix(path, changed)
            old_cwd = Path.cwd()
            try:
                os.chdir(directory)
                self.assertEqual(
                    load_prefix(Path("report.json"), prefix)["dut"],
                    directory / "prefix-1/dut/own.state",
                )
            finally:
                os.chdir(old_cwd)
            for field, value in (
                ("passed", False),
                ("repeatable", False),
                ("continuous_equivalence", False),
                ("provenance", {"observer": "stale"}),
                ("prefix_recipe", {}),
                ("sealed_files", {}),
            ):
                write_json(path, {**receipt, field: value})
                with self.subTest(field=field), self.assertRaises(ValueError):
                    load_prefix(path, prefix)
            write_json(path, receipt)
            (directory / "prefix-1/dut/own.state").write_bytes(b"changed")
            with self.assertRaisesRegex(ValueError, "changed"):
                load_prefix(path, prefix)
            (directory / "prefix-1/dut/own.state").unlink()
            with self.assertRaisesRegex(ValueError, "missing"):
                load_prefix(path, prefix)


if __name__ == "__main__":
    unittest.main()
