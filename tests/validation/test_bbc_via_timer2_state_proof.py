"""Reject restrictions that would hide arbitrary T2 input histories."""

import copy
import unittest

from tools.bbc.via_timer1_state_proof import initial_reset_assumption
from tools.bbc.via_timer2_state_proof import supporting_evidence


class Timer2AssumptionTests(unittest.TestCase):
    def graph(self):
        return {
            "modules": {
                "ViaTimer2Contract": {
                    "ports": {"reset": {"bits": [2]}},
                    "cells": {
                        "assume": {"type": "$assume", "connections": {"A": [2], "EN": [3]}},
                        "initial": {"type": "$initstate", "connections": {"Y": [3]}},
                    },
                }
            }
        }

    def test_initial_reset_admits_unrestricted_following_inputs(self):
        initial_reset_assumption(self.graph(), "ViaTimer2Contract")

    def test_pulse_mode_and_shift_constraints_are_rejected(self):
        graph = self.graph()
        cells = graph["modules"]["ViaTimer2Contract"]["cells"]
        cells["pulse_mode"] = copy.deepcopy(cells["assume"])
        with self.assertRaises(ValueError):
            initial_reset_assumption(graph, "ViaTimer2Contract")

    def test_reset_cannot_be_assumed_permanently(self):
        graph = self.graph()
        graph["modules"]["ViaTimer2Contract"]["cells"]["assume"]["connections"]["EN"] = ["1"]
        with self.assertRaises(ValueError):
            initial_reset_assumption(graph, "ViaTimer2Contract")

    def test_state_constraint_cannot_replace_reset(self):
        graph = self.graph()
        graph["modules"]["ViaTimer2Contract"]["cells"]["assume"]["connections"]["A"] = [9]
        with self.assertRaises(ValueError):
            initial_reset_assumption(graph, "ViaTimer2Contract")

    def test_incomplete_clock_support_is_rejected(self):
        import json
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "support.json"
            path.write_text(
                json.dumps(
                    {
                        "passed": True,
                        "inputs_unchanged": True,
                        "cases": 1536,
                        "observations": 83968,
                        "periods": [{"clock_hz": 32000000, "passed": True}],
                        "negative_control": {"detected": True},
                        "universal_via_equivalence": False,
                        "universal_game_equivalence": False,
                    }
                )
            )
            with self.assertRaisesRegex(ValueError, "Incomplete"):
                supporting_evidence(path)


if __name__ == "__main__":
    unittest.main()
