"""Guard the scheduler's source address inventory and sole reset assumption."""

import copy
import unittest

from tools.bbc.proofs.cpu_reference import parent_schedule


class ParentScheduleTests(unittest.TestCase):
    def test_truth_table_ranges_retain_boundaries_and_holes(self):
        for addresses, expected in (
            ([], "1'b0"),
            ([0], "(heldAddress>=16'h0000 && heldAddress<=16'h0000)"),
            ([65535], "(heldAddress>=16'hffff && heldAddress<=16'hffff)"),
            (
                [0, 1, 3, 65535],
                "(heldAddress>=16'h0000 && heldAddress<=16'h0001) || "
                "(heldAddress>=16'h0003 && heldAddress<=16'h0003) || "
                "(heldAddress>=16'hffff && heldAddress<=16'hffff)",
            ),
        ):
            bits = sum(1 << address for address in addresses)
            with self.subTest(addresses=addresses):
                self.assertEqual(parent_schedule.slow_expression(f"{bits:016384x}"), expected)

    def test_incomplete_or_invalid_native_tables_are_rejected(self):
        for table in ("", "0" * 16383, "0" * 16385, "g" * 16384):
            with self.subTest(length=len(table)), self.assertRaises(ValueError):
                parent_schedule.slow_expression(table)

    def graph(self):
        module = {
            "ports": {"reset": {"bits": [2]}},
            "netnames": {},
            "cells": {
                "initial": {"type": "$initstate", "connections": {"Y": [3]}},
                "reset_only": {"type": "$assume", "connections": {"A": [2], "EN": [3]}},
            },
        }
        for name, kind, start in (
            ("firstAddress", "$anyconst", 10),
            ("nextAddress", "$anyseq", 30),
        ):
            bits = list(range(start, start + 16))
            module["netnames"][name] = {"bits": bits, "attributes": {}}
            module["cells"][name] = {"type": kind, "connections": {"Y": bits}}
        return {"modules": {"Parent": module}}

    def test_additional_or_continuous_reset_assumptions_are_rejected(self):
        graph = self.graph()
        parent_schedule.guard_parent(graph, "Parent")
        for fault in ("extra", "continuous", "other-input", "initialized-address"):
            changed = copy.deepcopy(graph)
            module = changed["modules"]["Parent"]
            if fault == "extra":
                module["cells"]["extra"] = {"type": "$assume", "connections": {}}
            elif fault == "continuous":
                module["cells"]["reset_only"]["connections"]["EN"] = ["1"]
            elif fault == "other-input":
                module["cells"]["reset_only"]["connections"]["A"] = [10]
            else:
                module["netnames"]["nextAddress"]["attributes"]["init"] = "0" * 16
            with self.subTest(fault=fault), self.assertRaises(ValueError):
                parent_schedule.guard_parent(changed, "Parent")


if __name__ == "__main__":
    unittest.main()
