"""A relational CPU proof must bind every original register and reset action."""

import copy
import unittest

from tools.bbc.cpu_cold_preservation import relational_model


def graph(warm=False):
    ports = {
        "clk": {"direction": "input", "bits": [2]},
        "reset": {"direction": "input", "bits": [3]},
        "enable": {"direction": "input", "bits": [4]},
        "irq": {"direction": "input", "bits": [5]},
        "nmi": {"direction": "input", "bits": [6]},
        "read_data": {"direction": "input", "bits": list(range(7, 15))},
        "address": {"direction": "output", "bits": [15, 16]},
    }
    nets = {
        "core.state": {"bits": [15, 16], "attributes": {"init": "00"}},
        "reset_dummy": {"bits": [17, 18], "attributes": {"init": "10"}},
    }
    cells = {
        "state": {
            "type": "$adff",
            "connections": {"CLK": [2], "ARST": [3], "Q": [15, 16], "D": [7, 8]},
            "parameters": {"CLK_POLARITY": "1", "ARST_POLARITY": "1", "ARST_VALUE": "00"},
        },
        "dummy": {
            "type": "$dff",
            "connections": {"CLK": [2], "Q": [17, 18], "D": [9, 10]},
            "parameters": {"CLK_POLARITY": "1"},
        },
    }
    if warm:
        ports["warm_reset"] = {"direction": "input", "bits": [21]}
        for bit, name in ((19, "warm_pending"), (20, "warm_second")):
            nets[name] = {"bits": [bit], "attributes": {"init": "0"}}
            cells[name] = {
                "type": "$dff",
                "connections": {"CLK": [2], "Q": [bit], "D": ["0"]},
                "parameters": {"CLK_POLARITY": "1"},
            }
    return {"modules": {"BbcCpu": {"ports": ports, "netnames": nets, "cells": cells}}}


class CpuStateBindingTests(unittest.TestCase):
    def test_every_existing_register_bit_is_bound_without_exclusions(self):
        _, inventory = relational_model(graph(), graph(True))
        self.assertEqual(inventory["common_register_bits"], 4)
        self.assertEqual(inventory["excluded_existing_register_bits"], 0)
        self.assertEqual(inventory["new_register_bits"], 2)
        self.assertEqual(len(inventory["bindings"]), 4)

    def test_changed_asynchronous_reset_or_initial_value_is_rejected(self):
        for change in ("reset", "initial"):
            old, new = graph(), graph(True)
            module = new["modules"]["BbcCpu"]
            if change == "reset":
                module["cells"]["state"]["parameters"]["ARST_VALUE"] = "10"
            else:
                module["netnames"]["core.state"]["attributes"]["init"] = "10"
            with self.assertRaisesRegex(ValueError, "reset or initialization"):
                relational_model(old, new)

    def test_restart_control_must_start_inactive(self):
        new = graph(True)
        new["modules"]["BbcCpu"]["netnames"]["warm_pending"]["attributes"]["init"] = "1"
        with self.assertRaisesRegex(ValueError, "initialize inactive"):
            relational_model(graph(), new)

    def test_a_new_input_cannot_be_silently_ignored(self):
        new = graph(True)
        new["modules"]["BbcCpu"]["ports"]["unbound"] = {"direction": "input", "bits": [22]}
        with self.assertRaisesRegex(ValueError, "input relation"):
            relational_model(graph(), new)

    def test_duplicate_or_missing_state_binding_is_rejected(self):
        new = graph(True)
        new["modules"]["BbcCpu"]["netnames"]["core.state"]["bits"] = [17, 18]
        new["modules"]["BbcCpu"]["netnames"]["core.state"]["attributes"]["init"] = "10"
        with self.assertRaisesRegex(ValueError, "missing existing"):
            relational_model(graph(), new)

    def test_unrecognized_storage_primitive_cannot_escape_the_inventory(self):
        old = graph()
        cell = copy.deepcopy(old["modules"]["BbcCpu"]["cells"]["dummy"])
        cell["type"] = "$dffe"
        old["modules"]["BbcCpu"]["cells"]["dummy"] = cell
        with self.assertRaisesRegex(ValueError, "state primitive"):
            relational_model(old, graph(True))


if __name__ == "__main__":
    unittest.main()
