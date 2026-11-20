"""Keep callback-to-CPU proofs exhaustive and their clock interface explicit."""

import copy
import unittest
from pathlib import Path

from tools.bbc.proofs.cpu_reference import callback_cpu


class CallbackCpuTests(unittest.TestCase):
    def test_device_writes_cannot_be_connected_to_cpu_reset_inputs(self):
        source = """module Parent(input wire reset,input wire io_warmReset);
BbcCpuEvents cpu (
 .reset(reset),.warm_reset(io_warmReset)
);
Intel8271 controller (
 .reset(reset),.io_warmReset(io_warmReset)
);
endmodule
"""
        callback_cpu.reset_isolation(source, "Parent")
        for bad in (
            source.replace(".reset(reset)", ".reset(fdcReset)"),
            source.replace(".warm_reset(io_warmReset)", ".warm_reset(resetWrite)"),
            source.replace(".io_warmReset(io_warmReset)", ".io_warmReset(resetWrite)"),
            source.replace("Intel8271", "OtherController"),
            source.replace("input wire io_warmReset", "output wire io_warmReset"),
        ):
            with self.subTest(source=bad), self.assertRaises(ValueError):
                callback_cpu.reset_isolation(bad, "Parent")

    def test_projection_uses_the_executing_history_recipe_and_unique_constructor(self):
        prefix = """  wire [31:0] eventPipeline=referenceState[79:48] |
 ((age>=26 && ALLOW_NMI && (offNmiPin || riseNmiPin)) ? 32'h100:32'd0);
 wire [63:0] eventPins=(referenceState[167:104]&~64'h8000000) |
 ({63'd0,age>=26 && ALLOW_NMI && nmiPin}<<27);
 wire [255:0] eventState={referenceState[255:168],eventPins,
 referenceState[103:80],eventPipeline,referenceState[47:0]};
  M6502ReferenceNext source(
"""
        source = Path(callback_cpu.__file__).with_name("callback_cpu_source.v").read_text()
        text = callback_cpu.projection_contract(prefix, source)
        self.assertIn("assign equivalent=beforeA==eventState;", text)
        for bad in (
            source.replace("wire[255:0] beforeA", "wire[255:0] different"),
            source + "\nwire[255:0] beforeA=state;\n",
        ):
            with self.subTest(source=bad), self.assertRaises(ValueError):
                callback_cpu.projection_contract(prefix, bad)

    def test_history_controls_cannot_be_initialized_or_replaced_by_constants(self):
        body = {"cells": {}, "netnames": {}}
        cursor = 2
        for name, width, kind in (
            ("initialState", 256, "$anyconst"),
            ("initialCallback", 1, "$anyconst"),
            *[
                (n, 1, "$anyseq")
                for n in (
                    "callbackInput",
                    "acceptInput",
                    "acknowledge",
                    "commandEvent",
                    "abortEvent",
                    "resetRequest",
                )
            ],
        ):
            bits = list(range(cursor, cursor + width))
            cursor += width
            body["netnames"][name] = {"bits": bits, "attributes": {}}
            body["cells"][name] = {"type": kind, "connections": {"Y": bits}}
        graph = {"modules": {"PhysicalCallbackHistory": body}}
        callback_cpu.guard_history(graph)
        for fault in ("assumption", "constant", "initialization", "constant-reset"):
            bad = copy.deepcopy(graph)["modules"]["PhysicalCallbackHistory"]
            if fault == "assumption":
                bad["cells"]["restriction"] = {"type": "$assume"}
            elif fault == "constant":
                bad["cells"]["callbackInput"]["type"] = "$anyconst"
            elif fault == "constant-reset":
                bad["cells"]["resetRequest"]["type"] = "$anyconst"
            else:
                bad["netnames"]["acceptInput"]["attributes"]["init"] = "0"
            with self.subTest(fault=fault), self.assertRaises(ValueError):
                callback_cpu.guard_history({"modules": {"PhysicalCallbackHistory": bad}})

    def test_transition_inputs_cannot_be_narrowed_or_assumed(self):
        graph = {
            "modules": {
                "Probe": {
                    "ports": {"state": {"direction": "input", "bits": list(range(168))}},
                    "cells": {},
                }
            }
        }
        callback_cpu.guard_inputs(graph, "Probe", {"state": 168})
        for fault in ("width", "assumption", "hidden-state"):
            bad = copy.deepcopy(graph)
            body = bad["modules"]["Probe"]
            if fault == "width":
                body["ports"]["state"]["bits"].pop()
            else:
                body["cells"]["restriction"] = {
                    "type": "$assume" if fault == "assumption" else "$dff"
                }
            with self.subTest(fault=fault), self.assertRaises(ValueError):
                callback_cpu.guard_inputs(bad, "Probe", {"state": 168})

    def macro(self):
        return {
            "modules": {
                "NextMacro": {
                    "ports": {
                        "state_in": {"bits": list(range(100, 268))},
                        "next_out": {"bits": list(range(300, 468))},
                    },
                    "netnames": {
                        "core.NMI_1": {"bits": [186]},
                        "reset_dummy": {"bits": [250, 251]},
                    },
                }
            }
        }

    def test_only_the_named_previous_pin_field_is_normalized(self):
        self.assertEqual(
            callback_cpu.normalization_indices(self.macro()),
            {"core.NMI_1": [86], "reset_dummy": [150, 151]},
        )
        for fault in ("width", "alias", "missing-pin", "overlap"):
            graph = copy.deepcopy(self.macro())
            body = graph["modules"]["NextMacro"]
            if fault == "width":
                body["ports"]["state_in"]["bits"].pop()
            elif fault == "alias":
                body["ports"]["state_in"]["bits"][-1] = 100
            elif fault == "missing-pin":
                body["netnames"]["core.NMI_1"]["bits"] = [999]
            else:
                body["netnames"]["core.NMI_1"]["bits"] = [250]
            with self.subTest(fault=fault), self.assertRaises(ValueError):
                callback_cpu.normalization_indices(graph)

    def fdc(self):
        return {
            "modules": {
                "Intel8271": {
                    "ports": {
                        "clk": {"direction": "input", "bits": [2]},
                        "reset": {"direction": "input", "bits": [3]},
                        "io_storageData": {"direction": "input", "bits": [20]},
                        "io_write": {"direction": "input", "bits": [4]},
                        "io_storageAddress": {"direction": "output", "bits": [10]},
                    },
                    "netnames": {
                        "nmiLevel": {"bits": [10], "attributes": {"init": "0"}},
                        "nmiGap": {"bits": [11], "attributes": {"init": "0"}},
                        "eventNmi": {"bits": [40], "attributes": {}},
                        "commandNmi": {"bits": [41], "attributes": {}},
                        "abortNmi": {"bits": [10], "attributes": {}},
                    },
                    "attributes": {},
                    "cells": {
                        "level": {
                            "type": "$dff",
                            "parameters": {"CLK_POLARITY": "1"},
                            "connections": {"CLK": [2], "Q": [10], "D": [20]},
                        },
                        "gap": {
                            "type": "$dff",
                            "parameters": {"CLK_POLARITY": "1"},
                            "connections": {"CLK": [2], "Q": [11], "D": [11]},
                        },
                        "event": {
                            "type": "$or",
                            "port_directions": {"A": "input", "B": "input", "Y": "output"},
                            "connections": {"A": [10], "B": [20], "Y": [40]},
                        },
                        "command": {
                            "type": "$and",
                            "port_directions": {"A": "input", "B": "input", "Y": "output"},
                            "connections": {"A": [10], "B": [4], "Y": [41]},
                        },
                    },
                }
            }
        }

    def test_every_fdc_register_is_cut_and_actual_d_functions_remain(self):
        graph = callback_cpu.cut_fdc(self.fdc())
        body = graph["modules"]["FdcStateNext"]
        self.assertEqual(body["ports"]["proofState"]["bits"], [10, 11])
        self.assertEqual(body["ports"]["proofNext"]["bits"], [20, 11])
        self.assertTrue(all("init" not in net["attributes"] for net in body["netnames"].values()))
        self.assertEqual(
            callback_cpu.callback_support(body), (["io_storageData", "proofState"], ["proofState"])
        )

    def test_fdc_cut_rejects_unknown_storage_clock_or_duplicate_state(self):
        for fault in ("unknown", "clock", "duplicate"):
            graph = copy.deepcopy(self.fdc())
            cells = graph["modules"]["Intel8271"]["cells"]
            if fault == "unknown":
                cells["gap"]["type"] = "$latch"
            elif fault == "clock":
                cells["gap"]["connections"]["CLK"] = [5]
            else:
                cells["gap"]["connections"]["Q"] = [10]
            with self.subTest(fault=fault), self.assertRaises(ValueError):
                callback_cpu.cut_fdc(graph)

    def test_callback_cone_rejects_host_dependencies_and_nondeterminism(self):
        for fault in ("host", "anyseq", "undriven", "cycle", "duplicate", "unknown"):
            body = callback_cpu.cut_fdc(self.fdc())["modules"]["FdcStateNext"]
            cell = body["cells"]["event"]
            if fault == "host":
                cell["connections"]["B"] = [4]
            elif fault == "anyseq":
                cell.update(
                    type="$anyseq", port_directions={"Y": "output"}, connections={"Y": [40]}
                )
            elif fault == "undriven":
                cell["connections"]["B"] = [99]
            elif fault == "cycle":
                cell["connections"]["B"] = [40]
            elif fault == "duplicate":
                body["cells"]["other"] = copy.deepcopy(cell)
            else:
                cell["connections"]["B"] = ["x"]
            with self.subTest(fault=fault), self.assertRaises(ValueError):
                callback_cpu.callback_support(body)

    def test_support_lifts_each_unknown_as_a_distinct_free_input(self):
        body = callback_cpu.cut_fdc(self.fdc())["modules"]["FdcStateNext"]
        body["cells"]["event"]["connections"]["B"] = ["x"]
        body["cells"]["command"]["connections"]["B"] = ["x"]
        body["netnames"]["unused"] = {"bits": [99999]}
        lifted, count = callback_cpu.lift_support_unknowns(body)
        self.assertEqual(count, 2)
        bits = lifted["ports"]["proofUndefined"]["bits"]
        self.assertEqual(len(set(bits)), 2)
        self.assertTrue(all(bit > 99999 for bit in bits))
        self.assertEqual(lifted["cells"]["event"]["connections"]["B"], bits[:1])
        self.assertEqual(lifted["cells"]["command"]["connections"]["B"], bits[1:])
        self.assertEqual(body["cells"]["event"]["connections"]["B"], ["x"])
        self.assertEqual(lifted["ports"]["proofState"], body["ports"]["proofState"])

    def test_support_lifting_preserves_known_constants_and_rejects_unknown_inputs(self):
        body = callback_cpu.cut_fdc(self.fdc())["modules"]["FdcStateNext"]
        body["cells"]["event"]["connections"]["B"] = ["0"]
        lifted, count = callback_cpu.lift_support_unknowns(body)
        self.assertEqual(count, 0)
        self.assertNotIn("proofUndefined", lifted["ports"])
        self.assertEqual(lifted["cells"]["event"]["connections"]["B"], ["0"])
        for fault in ("input", "tristate"):
            changed = copy.deepcopy(body)
            if fault == "input":
                changed["ports"]["proofState"]["bits"][0] = "x"
            else:
                changed["cells"]["event"]["connections"]["B"] = ["z"]
            with self.subTest(fault=fault), self.assertRaises(ValueError):
                callback_cpu.lift_support_unknowns(changed)

    def test_support_lifting_exposes_a_direct_unknown_output(self):
        body = callback_cpu.cut_fdc(self.fdc())["modules"]["FdcStateNext"]
        body["ports"]["proofEvent"]["bits"] = ["x"]
        lifted, count = callback_cpu.lift_support_unknowns(body)
        self.assertEqual(count, 1)
        self.assertEqual(
            lifted["ports"]["proofEvent"]["bits"], lifted["ports"]["proofUndefined"]["bits"]
        )

    def test_abort_suppression_cannot_depend_on_host_pins(self):
        graph = self.fdc()
        graph["modules"]["Intel8271"]["netnames"]["abortNmi"]["bits"] = [4]
        body = callback_cpu.cut_fdc(graph)["modules"]["FdcStateNext"]
        with self.assertRaises(ValueError):
            callback_cpu.callback_support(body)


if __name__ == "__main__":
    unittest.main()
