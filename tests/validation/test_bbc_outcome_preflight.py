"""Outcome typos must fail before the expensive backend build or game replay."""

import json
import unittest
from unittest.mock import patch

from tools.bbc.run import main, validate_outcomes
from tools.validation.common import ROOT


class OutcomePreflightTests(unittest.TestCase):
    def scenario(self, field="docked", checkpoint="end"):
        return {
            "name": "fixture",
            "checkpoints": [{"name": "end", "at_us": 1}],
            "reference_assertions": [{"checkpoint": checkpoint, "field": field, "equals": True}],
        }

    def test_bundled_fields_cover_both_overlays_and_physical_ship_slots(self):
        cases = []
        for name in ["tools/validation/scenarios.json", "tools/bbc/scenarios.json"]:
            cases.extend(json.loads((ROOT / name).read_text())["scenarios"])
        cases.extend([self.scenario("universe.11.data.36"), self.scenario("weapons.cooldown")])
        validate_outcomes(cases)

    def test_typo_fails_after_asset_verification_before_backend_preparation(self):
        with (
            patch("sys.argv", ["bbc"]),
            patch("tools.bbc.run.compile_selected", return_value=[self.scenario("fuel")]),
            patch("tools.bbc.run.verify") as verify,
            patch("tools.bbc.cycle_reference.prepare") as prepare,
            patch("tools.bbc.run.native_current") as native,
            patch("tools.bbc.run.boot_cache.configure") as boot,
        ):
            with self.assertRaisesRegex(ValueError, "Unknown outcome field in fixture: fuel"):
                main()
            verify.assert_called_once()
            prepare.assert_not_called()
            native.assert_not_called()
            boot.assert_not_called()

    def test_bad_checkpoint_and_operator_are_rejected(self):
        cases = [self.scenario(checkpoint="missing"), self.scenario()]
        cases[1]["reference_assertions"][0]["at_least"] = 1
        for case in cases:
            with self.subTest(case=case), self.assertRaisesRegex(ValueError, "Invalid outcome"):
                validate_outcomes([case])

    def test_missing_and_out_of_range_fields_are_rejected(self):
        for name in ["universe.12.type", "equipment.missing", "universe.bad.type"]:
            with self.subTest(field=name), self.assertRaisesRegex(ValueError, "Unknown outcome"):
                validate_outcomes([self.scenario(name)])


if __name__ == "__main__":
    unittest.main()
