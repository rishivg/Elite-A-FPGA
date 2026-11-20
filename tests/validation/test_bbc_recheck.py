"""Capture reuse is limited to stronger assertions on unchanged, qualified data."""

import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.validation import bbc_recheck as recheck
from tools.validation.common import digest, write_json
from tools.validation.run import compile_scenario


class RecheckTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.run = self.root / "capture"
        self.source = self.root / "rtl/bbc/example.scala"
        self.source.parent.mkdir(parents=True)
        self.source.write_text("original captured RTL")
        self.scenario = {
            "name": "fixture",
            "duration_us": 10000,
            "events": [],
            "checkpoints": [{"name": "end", "at_us": 5000}],
            "reference_assertions": [{"checkpoint": "end", "field": "docked", "equals": True}],
        }
        self.contract = {"schema_version": 1, "actions": {}, "scenarios": [self.scenario]}
        write_json(self.root / recheck.CONTRACTS[0], self.contract)
        write_json(self.root / recheck.CONTRACTS[1], {**self.contract, "scenarios": []})
        self.addCleanup(patch.stopall)
        patch.object(recheck, "ROOT", self.root).start()
        patch.object(recheck, "runtime_current").start()
        self.compare = patch.object(
            recheck, "compare", return_value={"name": "fixture", "passed": True}
        ).start()
        inputs = {name: digest(self.root / name) for name in recheck.CONTRACTS}
        inputs["rtl/bbc/example.scala"] = digest(self.source)
        write_json(
            self.run / "manifest.json",
            {
                "target": recheck.TARGET,
                "inputs": inputs,
                "reference_options": {"reference_cpu": "cycle"},
            },
        )
        compiled = compile_scenario(self.scenario, {})
        compiled.update(unsupported_actions=[], dut_assertions=[])
        for suffix in ["", "-repeat"]:
            directory = self.run / ("fixture" + suffix)
            for backend in ["reference", "dut"]:
                path = directory / backend / "observation.bin"
                path.parent.mkdir(parents=True)
                path.write_bytes(b"unchanged capture bytes")
            write_json(directory / "scenario.json", compiled)
            self.receipt = {
                "name": "fixture",
                "passed": True,
                "repeatable": True,
                "repetitions": [{"passed": True}, {"passed": True}],
                "artifact_hashes": recheck.artifact_hashes(directory),
            }
            write_json(directory / "comparison.json", self.receipt)
        self.report = {
            "strict_matrix_passed": True,
            "boot_provenance_complete": True,
            "inputs_unchanged": True,
            "results": [self.receipt],
            "remaining": ["Physical board proof"],
        }
        write_json(self.run / "report.json", self.report)
        recheck.seal_contracts(self.run)

    def write_contract(self, contract):
        write_json(self.root / recheck.CONTRACTS[0], contract)

    def test_recheck_runs_both_comparisons_without_claiming_guest_execution(self):
        result = recheck.recheck(self.run)
        self.assertTrue(result["strict_matrix_passed"])
        self.assertFalse(result["dut_executed_this_run"])
        self.assertEqual(self.compare.call_count, 2)
        self.assertEqual(result["remaining"], self.report["remaining"])

    def test_stronger_assertions_are_evaluated_on_both_captures(self):
        current = copy.deepcopy(self.contract)
        current["scenarios"][0]["reference_assertions"].append(
            {"checkpoint": "end", "field": "shields.0", "equals": 96}
        )
        self.write_contract(current)
        self.assertTrue(recheck.recheck(self.run)["strict_matrix_passed"])
        for call in self.compare.call_args_list:
            self.assertEqual(len(call.args[1]["reference_assertions"]), 2)

    def test_removing_or_weakening_an_assertion_is_rejected(self):
        for assertions in [[], [{"checkpoint": "end", "field": "docked", "equals": False}]]:
            current = copy.deepcopy(self.contract)
            current["scenarios"][0]["reference_assertions"] = assertions
            self.write_contract(current)
            with self.assertRaisesRegex(ValueError, "removed or weakened"):
                recheck.recheck(self.run)
        self.compare.assert_not_called()

    def test_changed_input_schedule_duration_checkpoint_or_scenario_set_is_rejected(self):
        for field, value in [
            ("events", [{"at_us": 1000, "actions": []}]),
            ("duration_us", 12000),
            ("checkpoints", [{"at_us": 6000, "name": "end"}]),
            ("name", "different"),
        ]:
            current = copy.deepcopy(self.contract)
            current["scenarios"][0][field] = value
            self.write_contract(current)
            with self.assertRaisesRegex(ValueError, "schedule changed"):
                recheck.recheck(self.run)
        self.write_contract({**self.contract, "scenarios": []})
        with self.assertRaisesRegex(ValueError, "Scenario set changed"):
            recheck.recheck(self.run)
        self.compare.assert_not_called()

    def test_changed_source_or_contract_snapshot_is_rejected(self):
        self.source.write_text("changed RTL")
        with self.assertRaisesRegex(ValueError, "source changed"):
            recheck.recheck(self.run)
        self.source.write_text("original captured RTL")
        snapshot = self.run / "contract-sources" / recheck.CONTRACTS[0]
        snapshot.write_text("{}")
        with self.assertRaisesRegex(ValueError, "snapshot changed"):
            recheck.recheck(self.run)

    def test_mutated_missing_or_extra_capture_files_are_rejected(self):
        path = self.run / "fixture-repeat/dut/observation.bin"
        path.write_bytes(b"mutated")
        with self.assertRaisesRegex(ValueError, "Captured bytes changed"):
            recheck.recheck(self.run)
        path.unlink()
        with self.assertRaisesRegex(ValueError, "Captured bytes changed"):
            recheck.recheck(self.run)
        path.write_bytes(b"unchanged capture bytes")
        (path.parent / "extra.bin").write_bytes(b"extra")
        with self.assertRaisesRegex(ValueError, "Captured bytes changed"):
            recheck.recheck(self.run)

    def test_unqualified_empty_or_failed_capture_matrix_is_rejected(self):
        for key in ["strict_matrix_passed", "boot_provenance_complete", "inputs_unchanged"]:
            write_json(self.run / "report.json", {**self.report, key: False})
            with self.assertRaisesRegex(ValueError, "complete, repeated"):
                recheck.recheck(self.run)
        write_json(self.run / "report.json", {**self.report, "results": []})
        with self.assertRaisesRegex(ValueError, "Missing, extra"):
            recheck.recheck(self.run)
        write_json(
            self.run / "report.json",
            {**self.report, "results": [{**self.receipt, "repeatable": False}]},
        )
        with self.assertRaisesRegex(ValueError, "did not pass twice"):
            recheck.recheck(self.run)

    def test_changed_recorded_scenario_and_failed_second_comparison_are_not_hidden(self):
        recorded = self.run / "fixture-repeat/scenario.json"
        original = recorded.read_bytes()
        recorded.write_text("{}")
        with self.assertRaisesRegex(ValueError, "Recorded scenario changed"):
            recheck.recheck(self.run)
        recorded.write_bytes(original)
        self.compare.side_effect = [
            {"name": "fixture", "passed": True},
            {"name": "fixture", "passed": False},
        ]
        self.assertFalse(recheck.recheck(self.run)["strict_matrix_passed"])

    def test_snapshot_cannot_be_created_after_original_contract_bytes_are_lost(self):
        snapshot = self.run / "contract-sources" / recheck.CONTRACTS[0]
        snapshot.unlink()
        self.write_contract({**self.contract, "scenarios": []})
        with self.assertRaisesRegex(ValueError, "Original contract unavailable"):
            recheck.seal_contracts(self.run)


if __name__ == "__main__":
    unittest.main()
