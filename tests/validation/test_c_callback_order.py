"""Reject callback proofs that observe a different pin, clock or state cut."""

import copy
import unittest

from tools.bbc.proofs.cpu_reference import callback_order

SOURCE = """module Parent (input wire clk,input wire reset);
  BbcCpu cpu (
    .nmi(discNmi),.irq(cpu_irq)
  );
  Intel8271 fdc (
    .clk(clk),.reset(reset),.io_nmi(discNmi)
  );
  assign cpu_irq = ((sys_io_irq || user_io_irq) || serial_io_irq);
endmodule
module Intel8271 (
input wire io_read,input wire io_write,input wire io_warmReset,
input wire [2:0] io_address,input wire [7:0] io_data,
input wire [15:0] io_storageData,input wire clk,input wire reset
);
assign io_nmi=eventNmi && !io_warmReset;
endmodule
"""


class CallbackBindingTests(unittest.TestCase):
    def test_changed_cpu_interrupt_sources_are_rejected(self):
        _, extra = callback_order.fdc_instance(SOURCE, "Parent")
        self.assertEqual(extra, [])
        for altered in (
            SOURCE.replace(".nmi(discNmi)", ".nmi(1'b0)"),
            SOURCE.replace("|| serial_io_irq", "|| 1'b0"),
            SOURCE.replace("Intel8271 fdc (", "Intel8271 fdc (\n);\nIntel8271 another ("),
        ):
            with self.subTest(source=altered), self.assertRaises(ValueError):
                callback_order.fdc_instance(altered, "Parent")

    def test_nonstandard_controller_clock_enable_is_rejected(self):
        source = SOURCE.replace(".io_nmi(discNmi)", ".io_nmi(discNmi),.enable32(clock32)")
        source = source.replace("input wire io_read", "input wire enable32,input wire io_read")
        with self.assertRaisesRegex(ValueError, "clock enable"):
            callback_order.fdc_instance(source, "Parent")


class ViaCutTests(unittest.TestCase):
    def graph(self):
        module = {
            "ports": {"clk": {"bits": [2]}, "reset": {"bits": [3]}},
            "netnames": {},
            "cells": {},
            "attributes": {},
        }
        for i, name in enumerate(("flags", "enabled")):
            bits = list(range(10 + 7 * i, 17 + 7 * i))
            module["netnames"][name] = {"bits": bits, "attributes": {"init": "0" * 7}}
            module["cells"][name] = {
                "type": "$dff",
                "parameters": {"CLK_POLARITY": "1"},
                "connections": {"CLK": [2], "Q": bits, "D": [bit + 30 for bit in bits]},
            }
        return {"modules": {"Via6522": module}}

    def test_all_state_is_free_and_flag_mask_d_functions_are_retained(self):
        graph = callback_order.via_cut(self.graph())
        module = graph["modules"]["ViaNext"]
        self.assertEqual(len(module["ports"]["proofState"]["bits"]), 14)
        self.assertEqual(module["ports"]["proofNextflags"]["bits"], list(range(40, 47)))
        self.assertFalse(module["cells"])
        self.assertTrue(all("init" not in net["attributes"] for net in module["netnames"].values()))

    def test_unknown_storage_clock_or_duplicate_q_is_rejected(self):
        for fault in ("clock", "unknown-storage", "duplicate-q", "wrong-width", "reset"):
            graph = copy.deepcopy(self.graph())
            module = graph["modules"]["Via6522"]
            if fault == "clock":
                module["cells"]["flags"]["connections"]["CLK"] = [4]
            elif fault == "unknown-storage":
                module["cells"]["other"] = {"type": "$dlatch"}
            elif fault == "duplicate-q":
                module["cells"]["duplicate"] = copy.deepcopy(module["cells"]["flags"])
            elif fault == "wrong-width":
                module["netnames"]["flags"]["bits"] = [10]
            else:
                cell = module["cells"]["flags"]
                cell["type"] = "$adff"
                cell["connections"]["ARST"] = [4]
                cell["parameters"]["ARST_POLARITY"] = "1"
            with self.subTest(fault=fault), self.assertRaises(ValueError):
                callback_order.via_cut(graph)


if __name__ == "__main__":
    unittest.main()
