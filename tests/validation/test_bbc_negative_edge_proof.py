"""Clock-use checks reject a falling-edge, clock-derived or opaque state path."""

import copy
import unittest

from tools.bbc.negative_edge_proof import clock_property


class NegativeEdgeProofTests(unittest.TestCase):
    def graph(self):
        return {
            "modules": {
                "Machine": {
                    "ports": {
                        "clk": {"direction": "input", "bits": [2]},
                        "output": {"direction": "output", "bits": [3]},
                    },
                    "cells": {
                        "state": {
                            "type": "$adffe",
                            "connections": {"CLK": [2], "D": [4], "Q": [3]},
                            "parameters": {"CLK_POLARITY": "1"},
                        },
                        "memory": {
                            "type": "$mem_v2",
                            "connections": {"RD_CLK": [2, "x"], "WR_CLK": [2]},
                            "parameters": {
                                "RD_CLK_ENABLE": "01",
                                "RD_CLK_POLARITY": "01",
                                "WR_CLK_ENABLE": "1",
                                "WR_CLK_POLARITY": "1",
                            },
                        },
                    },
                }
            }
        }

    def test_positive_state_and_memory_clocks_pass(self):
        graph = self.graph()
        before = copy.deepcopy(graph)
        uses = clock_property(graph, "Machine")
        self.assertEqual(len(uses), 3)
        self.assertEqual(graph, before)

    def test_negative_state_or_memory_clock_is_rejected(self):
        for name, parameter in [("state", "CLK_POLARITY"), ("memory", "WR_CLK_POLARITY")]:
            with self.subTest(name=name):
                graph = self.graph()
                graph["modules"]["Machine"]["cells"][name]["parameters"][parameter] = "0"
                with self.assertRaises(ValueError):
                    clock_property(graph, "Machine")

    def test_combinational_output_or_data_clock_use_is_rejected(self):
        graph = self.graph()
        graph["modules"]["Machine"]["ports"]["output"]["bits"] = [2]
        with self.assertRaisesRegex(ValueError, "directly affects output"):
            clock_property(graph, "Machine")
        graph = self.graph()
        graph["modules"]["Machine"]["cells"]["state"]["connections"]["D"] = [2]
        with self.assertRaisesRegex(ValueError, "unsupported root-clock use"):
            clock_property(graph, "Machine")

    def test_latches_empty_clock_cones_and_blackboxes_are_rejected(self):
        for cells in (
            {},
            {"latch": {"type": "$dlatch", "connections": {"EN": [2]}, "parameters": {}}},
            {"opaque": {"type": "HiddenMachine", "connections": {}, "parameters": {}}},
        ):
            with self.subTest(cells=cells):
                graph = self.graph()
                graph["modules"]["Machine"]["cells"] = cells
                with self.assertRaises(ValueError):
                    clock_property(graph, "Machine")


if __name__ == "__main__":
    unittest.main()
