"""Reject state-dependent or persistent reset assumptions in the T1 proof."""

import copy
import unittest

from tools.bbc.via_timer1_state_proof import initial_reset_assumption


class Timer1AssumptionTests(unittest.TestCase):
    def graph(self):
        return {
            "modules": {
                "ViaTimer1Contract": {
                    "ports": {"reset": {"bits": [2]}},
                    "cells": {
                        "assume": {"type": "$assume", "connections": {"A": [2], "EN": [4]}},
                        "initial": {"type": "$initstate", "connections": {"Y": [3]}},
                        "enable": {"type": "$and", "connections": {"A": ["1"], "B": [3], "Y": [4]}},
                    },
                }
            }
        }

    def test_only_the_initial_reset_is_admitted(self):
        graph = self.graph()
        initial_reset_assumption(graph)
        graph["modules"]["ViaTimer1Contract"]["cells"]["assume"]["connections"]["EN"] = [3]
        initial_reset_assumption(graph)

    def test_state_constraints_and_extra_assumptions_are_rejected(self):
        for target in ("state", "additional"):
            with self.subTest(target=target), self.assertRaises(ValueError):
                graph = self.graph()
                cells = graph["modules"]["ViaTimer1Contract"]["cells"]
                if target == "state":
                    cells["assume"]["connections"]["A"] = [9]
                else:
                    cells["extra"] = copy.deepcopy(cells["assume"])
                initial_reset_assumption(graph)

    def test_reset_cannot_remain_assumed_after_initialization(self):
        graph = self.graph()
        graph["modules"]["ViaTimer1Contract"]["cells"]["assume"]["connections"]["EN"] = ["1"]
        with self.assertRaises(ValueError):
            initial_reset_assumption(graph)

    def test_initial_reset_cannot_depend_on_a_state_or_input_condition(self):
        graph = self.graph()
        graph["modules"]["ViaTimer1Contract"]["cells"]["enable"]["connections"]["A"] = [9]
        with self.assertRaises(ValueError):
            initial_reset_assumption(graph)


if __name__ == "__main__":
    unittest.main()
