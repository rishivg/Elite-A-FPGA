"""Reject constrained or aliased inputs before source-preservation partitioning."""

import copy
import unittest

from tools.bbc.proofs.cpu_reference.preservation_partitions import expose_inputs


def design():
    state, pins = list(range(2, 258)), list(range(258, 322))
    cells = {
        name: {
            "type": "$anyseq",
            "parameters": {"WIDTH": f"{len(bits):032b}"},
            "port_directions": {"Y": "output"},
            "connections": {"Y": bits},
        }
        for name, bits in (("state", state), ("pins", pins))
    }
    return {
        "modules": {
            "DocumentedModelContract": {
                "ports": {
                    "equivalent": {"direction": "output", "bits": ["1"]},
                    "context": {"direction": "output", "bits": ["1"]},
                },
                "netnames": {"state_in": {"bits": state}, "pins_in": {"bits": pins}},
                "cells": cells,
            }
        }
    }


class PreservationInputTests(unittest.TestCase):
    def test_free_vectors_become_full_public_inputs_without_changing_outputs(self):
        original = design()
        expected = copy.deepcopy(original["modules"]["DocumentedModelContract"])
        changed = expose_inputs(original)["modules"]["DocumentedModelContract"]
        self.assertEqual(changed["ports"]["equivalent"], expected["ports"]["equivalent"])
        self.assertEqual(changed["ports"]["context"], expected["ports"]["context"])
        self.assertEqual(changed["netnames"], expected["netnames"])
        self.assertEqual(changed["ports"]["state_in"]["bits"], list(range(2, 258)))
        self.assertEqual(changed["ports"]["pins_in"]["bits"], list(range(258, 322)))
        self.assertEqual(changed["cells"], {})

    def test_constraints_state_or_extra_symbolic_inputs_are_rejected(self):
        for kind in (
            "$assume",
            "$check",
            "$assert",
            "$anyconst",
            "$anyseq",
            "$dff",
            "$_DFF_P_",
            "$dlatch",
        ):
            data = design()
            data["modules"]["DocumentedModelContract"]["cells"]["extra"] = {
                "type": kind,
                "connections": {"Y": [400]},
            }
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                expose_inputs(data)

    def test_aliased_constant_or_incomplete_input_vectors_are_rejected(self):
        for bits in (list(range(2, 66)), [258] * 64, ["0"] * 64, list(range(258, 321))):
            data = design()
            data["modules"]["DocumentedModelContract"]["netnames"]["pins_in"]["bits"] = bits
            with self.subTest(bits=bits), self.assertRaises(ValueError):
                expose_inputs(data)

    def test_wrong_property_direction_or_driver_width_is_rejected(self):
        for changed in ("port", "width"):
            data = design()
            module = data["modules"]["DocumentedModelContract"]
            if changed == "port":
                module["ports"]["equivalent"]["direction"] = "input"
            else:
                module["cells"]["pins"]["parameters"]["WIDTH"] = f"{32:032b}"
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                expose_inputs(data)


if __name__ == "__main__":
    unittest.main()
