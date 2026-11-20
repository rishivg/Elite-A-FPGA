"""Decoder repairs can reuse sealed observations without changing execution."""

import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.validation import bbc_recheck, bbc_recompare as replay
from tools.validation.common import digest, write_json


class RecomparisonTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.run = self.root / "capture"
        self.output = self.root / "new-comparison"
        self.source = self.root / "capture-runtime.cpp"
        self.source.write_text("unchanged guest execution")
        self.decoder = self.root / replay.COMPARATOR
        self.decoder.parent.mkdir(parents=True)
        self.decoder.write_text("old comparison code")
        self.boot = self.root / "own-boot.json"
        self.boot.write_text("independently executed own boots")
        self.scenario = {
            "name": "fixture",
            "duration_us": 10000,
            "events": [],
            "checkpoints": [{"name": "end", "at_us": 5000}],
            "reference_assertions": [{"checkpoint": "end", "field": "docked", "equals": True}],
        }
        self.contract = {"schema_version": 1, "actions": {}, "scenarios": [self.scenario]}
        for name, scenarios in zip(replay.CONTRACTS, [[self.scenario], []]):
            write_json(self.root / name, {**self.contract, "scenarios": scenarios})
        self.addCleanup(patch.stopall)
        patch.object(replay, "ROOT", self.root).start()
        patch.object(bbc_recheck, "runtime_current").start()
        patch.object(replay.boot_cache, "configure").start()
        self.boot_valid = patch.object(replay.boot_cache, "valid", return_value=True).start()
        patch.object(replay.boot_cache, "RECEIPT", self.boot).start()
        self.compare = patch.object(replay, "compare", side_effect=self.comparison).start()
        self.manifest = {
            "target": replay.TARGET,
            "reference_options": {
                "reference_cpu": "cycle",
                "drive_profile": "clocked-fm",
                "power_profile": "shared-reset",
            },
            "inputs": {
                name: digest(self.root / name)
                for name in [*replay.CONTRACTS, replay.COMPARATOR, self.source.name]
            },
        }
        write_json(self.run / "manifest.json", self.manifest)
        compiled = replay.compile_selected(self.contract)[0]
        for suffix in ["", "-repeat"]:
            directory = self.run / ("fixture" + suffix)
            write_json(directory / "scenario.json", compiled)
            for backend, clocks in [("reference", 20000), ("dut", 320000)]:
                path = directory / backend
                write_json(path / "summary.json", {"completed": True, "duration_cycles": clocks})
                (path / "observation.bin").write_bytes(b"original immutable observations")
                (path / "end.state.json").write_text("old derived state")
                (path / "end.png").write_bytes(b"old derived image")

    def comparison(self, directory, scenario):
        for backend in ["reference", "dut"]:
            self.assertEqual(
                (directory / backend / "observation.bin").read_bytes(),
                b"original immutable observations",
            )
            self.assertTrue((directory / backend / "observation.bin").is_symlink())
            (directory / backend / "end.state.json").write_text("corrected derived state")
            (directory / backend / "end.png").write_bytes(b"corrected derived image")
        return {"name": scenario["name"], "passed": True}

    def test_repaired_decoder_compares_both_guests_without_mutating_originals(self):
        replay.seal(self.run)
        original = replay.observations(self.run, replay.scenarios(self.run))
        self.decoder.write_text("corrected decoder")
        result = replay.recompare(self.run, self.output)
        self.assertTrue(result["strict_matrix_passed"])
        self.assertFalse(result["dut_executed_this_run"])
        self.assertNotEqual(
            result["original_comparator_sha256"], result["current_comparator_sha256"]
        )
        self.assertEqual(self.compare.call_count, 2)
        self.assertEqual(original, replay.observations(self.run, replay.scenarios(self.run)))
        self.assertEqual((self.run / "fixture/dut/end.state.json").read_text(), "old derived state")

    def repair(self):
        return {
            "scenario": "fixture",
            "reason": "Independent original discovery identified the incorrect expected value",
            "before": self.scenario["reference_assertions"][0],
            "after": {"checkpoint": "end", "field": "docked", "equals": False},
        }

    def test_declared_outcome_repair_retains_original_contract_and_both_observations(self):
        replay.seal(self.run)
        repaired = self.repair()
        result = replay.recompare(self.run, self.output, [repaired])
        self.assertEqual(result["outcome_repairs"], [repaired])
        self.assertTrue(result["strict_matrix_passed"])
        self.assertEqual(
            self.compare.call_args.args[1]["reference_assertions"], [repaired["after"]]
        )
        self.assertEqual(
            replay.scenarios(self.run)[0]["reference_assertions"], [repaired["before"]]
        )

    def test_outcome_repair_cannot_add_physical_inputs_or_change_checkpoints(self):
        compiled = replay.scenarios(self.run)
        for key, value in [("events", []), ("checkpoint", "different"), ("at_us", 1)]:
            repair = self.repair()
            repair["after"][key] = value
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, "only replace"):
                replay.repaired_scenarios(compiled, [repair])

    def test_outcome_repair_cannot_delete_assertions_or_hide_scope(self):
        compiled = replay.scenarios(self.run)
        for edit in [
            lambda r: r.update(after={}),
            lambda r: r.update(reason=""),
            lambda r: r.update(duration_us=1),
        ]:
            repair = self.repair()
            edit(repair)
            with self.assertRaises(ValueError):
                replay.repaired_scenarios(compiled, [repair])

    def test_outcome_repair_rejects_unknown_scenario_and_nonexistent_old_assertion(self):
        compiled = replay.scenarios(self.run)
        for key, value in [
            ("scenario", "missing"),
            ("before", {"checkpoint": "end", "field": "docked", "equals": "missing"}),
        ]:
            repair = self.repair()
            repair[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                replay.repaired_scenarios(compiled, [repair])

    def test_sealing_requires_all_original_sources_including_decoder(self):
        self.decoder.write_text("changed before sealing")
        with self.assertRaisesRegex(ValueError, "Capture source changed"):
            replay.seal(self.run)

    def test_other_capture_source_or_contract_changes_require_new_execution(self):
        replay.seal(self.run)
        for path in [self.source, self.root / replay.CONTRACTS[0]]:
            saved = path.read_bytes()
            path.write_text("changed source")
            with self.assertRaisesRegex(ValueError, "Capture source changed"):
                replay.recompare(self.run, self.output)
            path.write_bytes(saved)
        self.compare.assert_not_called()

    def test_missing_extra_or_mutated_original_observations_are_rejected(self):
        replay.seal(self.run)
        path = self.run / "fixture-repeat/dut/observation.bin"
        saved = path.read_bytes()
        path.write_bytes(b"mutated")
        with self.assertRaisesRegex(ValueError, "Original observations changed"):
            replay.recompare(self.run, self.output)
        path.unlink()
        with self.assertRaisesRegex(ValueError, "Original observations changed"):
            replay.recompare(self.run, self.output)
        path.write_bytes(saved)
        (path.parent / "extra.bin").write_bytes(b"extra")
        with self.assertRaisesRegex(ValueError, "Original observations changed"):
            replay.recompare(self.run, self.output)
        self.compare.assert_not_called()

    def test_partial_missing_or_changed_scenario_cannot_be_sealed(self):
        directory = self.run / "fixture-repeat"
        summary = directory / "dut/summary.json"
        write_json(summary, {"completed": False, "duration_cycles": 320000})
        with self.assertRaisesRegex(ValueError, "Incomplete capture"):
            replay.seal(self.run)
        write_json(summary, {"completed": True, "duration_cycles": 319999})
        with self.assertRaisesRegex(ValueError, "Incomplete capture"):
            replay.seal(self.run)
        write_json(summary, {"completed": True, "duration_cycles": 320000})
        scenario = directory / "scenario.json"
        scenario.unlink()
        with self.assertRaisesRegex(ValueError, "missing or extra cases"):
            replay.seal(self.run)
        write_json(scenario, {**self.scenario, "duration_us": 12000})
        with self.assertRaisesRegex(ValueError, "Recorded scenario changed"):
            replay.seal(self.run)

    def test_unverified_changed_boots_or_wrong_reference_mode_are_rejected(self):
        self.boot_valid.return_value = False
        with self.assertRaisesRegex(ValueError, "boots are stale"):
            replay.seal(self.run)
        self.boot_valid.return_value = True
        replay.seal(self.run)
        self.boot.write_text("changed own state provenance")
        with self.assertRaisesRegex(ValueError, "changed provenance"):
            replay.recompare(self.run, self.output)
        changed = copy.deepcopy(self.manifest)
        changed["reference_options"]["reference_cpu"] = "jsbeeb"
        write_json(self.run / "manifest.json", changed)
        with self.assertRaisesRegex(ValueError, "independent cycle reference"):
            replay.recompare(self.run, self.output)

    def test_original_capture_changes_during_comparison_fail(self):
        replay.seal(self.run)

        def mutate(directory, scenario):
            result = self.comparison(directory, scenario)
            (self.run / "fixture/dut/end.state.json").write_text("mutated during comparison")
            return result

        self.compare.side_effect = mutate
        with self.assertRaisesRegex(ValueError, "Inputs changed during recomparison"):
            replay.recompare(self.run, self.output)

    def test_decoder_changes_during_comparison_fail(self):
        replay.seal(self.run)

        def mutate(directory, scenario):
            result = self.comparison(directory, scenario)
            self.decoder.write_text("changed while comparing")
            return result

        self.compare.side_effect = mutate
        with self.assertRaisesRegex(ValueError, "Inputs changed during recomparison"):
            replay.recompare(self.run, self.output)

    def test_a_failed_comparison_cannot_be_a_strict_matrix_pass(self):
        replay.seal(self.run)
        self.compare.return_value = {"name": "fixture", "passed": False}
        self.compare.side_effect = None
        self.assertFalse(replay.recompare(self.run, self.output)["strict_matrix_passed"])

    def test_matching_outcomes_do_not_hide_changed_repetition_bytes(self):
        (self.run / "fixture-repeat/dut/observation.bin").write_bytes(b"different repetition")
        replay.seal(self.run)
        self.compare.side_effect = None
        self.compare.return_value = {"name": "fixture", "passed": True}
        result = replay.recompare(self.run, self.output)
        self.assertFalse(result["strict_matrix_passed"])
        self.assertFalse(result["results"][0]["repeatable"])

    def test_seal_changes_during_comparison_fail(self):
        replay.seal(self.run)

        def mutate(directory, scenario):
            result = self.comparison(directory, scenario)
            seal = self.run / replay.SEAL
            seal.write_text(seal.read_text() + "\n")
            return result

        self.compare.side_effect = mutate
        with self.assertRaisesRegex(ValueError, "Inputs changed during recomparison"):
            replay.recompare(self.run, self.output)

    def test_redirected_output_link_cannot_reuse_the_original_hash(self):
        replay.seal(self.run)

        def redirect(directory, scenario):
            result = self.comparison(directory, scenario)
            link = directory / "dut/observation.bin"
            link.unlink()
            link.symlink_to(self.run / "fixture-repeat/dut/observation.bin")
            return result

        self.compare.side_effect = redirect
        with self.assertRaisesRegex(ValueError, "Borrowed observation changed"):
            replay.recompare(self.run, self.output)

    def test_boot_guard_uses_the_captured_adc_profile(self):
        with patch.object(replay.boot_cache, "configure") as configure:
            replay.own_boots(self.manifest)
            configure.assert_called_with("cycle", "clocked-fm", "shared-reset", "original")
            self.manifest["reference_options"]["adc_profile"] = "bbc-result-bits"
            replay.own_boots(self.manifest)
            configure.assert_called_with("cycle", "clocked-fm", "shared-reset", "bbc-result-bits")
            self.manifest["reference_options"]["adc_profile"] = "unknown"
            with self.assertRaisesRegex(ValueError, "Unknown captured ADC"):
                replay.own_boots(self.manifest)


if __name__ == "__main__":
    unittest.main()
