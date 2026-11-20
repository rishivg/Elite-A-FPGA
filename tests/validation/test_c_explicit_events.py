"""The event-port refinement must preserve all CPU state and source identity."""

import copy
import subprocess
import unittest

from tests.validation.test_c_callback_order import SOURCE
from tools.bbc import bus_binding_proof, cpu_events
from tools.bbc.proofs.cpu_reference import callback_order, explicit_events


class EventCpuDerivationTests(unittest.TestCase):
    def test_actual_controller_event_must_reach_the_cpu_only_on_base_edges(self):
        source = (
            SOURCE.replace("BbcCpu cpu", "BbcCpuEvents cpu")
            .replace(
                ".nmi(discNmi),.irq(cpu_irq)", ".nmi(discNmi),.irq(cpu_irq),.nmi_event(cpuEvent)"
            )
            .replace(".io_nmi(discNmi)", ".io_nmi(discNmi),.io_nmiEvent(fdcEvent)")
        )
        source = source.replace(
            "endmodule", "assign cpuEvent = (fdcEvent && clock32);\nendmodule", 1
        )
        callback_order.fdc_instance(source, "Parent")
        for bad in (
            source.replace("fdcEvent && clock32", "fdcEvent && 1'b1"),
            source.replace(",.nmi_event(cpuEvent)", ""),
            source.replace(".nmi_event(cpuEvent)", ".nmi_event(otherEvent)"),
        ):
            with self.subTest(source=bad), self.assertRaises(ValueError):
                callback_order.fdc_instance(bad, "Parent")

    def test_new_or_missing_edge_consumers_fail_closed(self):
        core = "module bbc_cpu_ce(clk);\n" + "wire edge = (NMI && !NMI_1);\n" * 6 + "endmodule\n"
        wrapper = "module BbcCpu(input clk);\nbbc_cpu_ce core(.BBC_P(flags));\nendmodule\n"
        derived, connected = cpu_events.derive(core, wrapper)
        self.assertIn("module bbc_cpu_events", derived)
        self.assertIn(".BBC_NMI_EVENT(nmi_event)", connected)
        for bad_core, bad_wrapper in (
            (core.replace("wire edge = (NMI && !NMI_1);\n", "", 1), wrapper),
            (core + "wire extra = NMI & ~NMI_1;", wrapper),
            (core, wrapper.replace(".BBC_P(flags));", ".BBC_P(other));")),
        ):
            with self.subTest(core=bad_core), self.assertRaises(ValueError):
                cpu_events.derive(bad_core, bad_wrapper)

    def test_parent_binding_accepts_the_event_cpu_without_hiding_duplicate_instances(self):
        parent = "BbcCpuEvents cpu (\n .nmi(nmi),.nmi_event(event)\n);"
        self.assertEqual(bus_binding_proof.ports(parent, "BbcCpu", "cpu")["nmi_event"], "event")
        for bad in (
            parent + "\n" + parent.replace("BbcCpuEvents", "BbcCpu"),
            parent.replace("BbcCpuEvents", "OtherCpu"),
        ):
            with self.subTest(source=bad), self.assertRaises(ValueError):
                bus_binding_proof.ports(bad, "BbcCpu", "cpu")


class EventCpuCutTests(unittest.TestCase):
    def models(self):
        old = {"ports": {"state_in": {"bits": list(range(100, 268))}}, "netnames": {}}
        original = {"netnames": {}}
        body = {
            "ports": {"clk": {"bits": [2]}, "reset": {"bits": [3]}},
            "netnames": {},
            "cells": {},
            "attributes": {},
        }
        for i in range(168):
            name = "field" + str(i)
            old["netnames"][name] = {"bits": [100 + i]}
            original["netnames"][name] = {"bits": [100 + i], "attributes": {"init": "0"}}
            body["netnames"][name] = {"bits": [400 + i], "attributes": {"init": "0"}}
            body["cells"][name] = {
                "type": "$dff",
                "parameters": {"CLK_POLARITY": "1"},
                "connections": {"CLK": [2], "Q": [400 + i], "D": [400 + i]},
            }
        return (
            {"modules": {"BbcCpuEvents": body}},
            {"modules": {"NextActual": old}},
            {"modules": {"BbcCpu": original}},
        )

    def test_complete_state_and_initialization_are_retained(self):
        actual, legacy, original = self.models()
        result = explicit_events.cut(actual, legacy, original)["modules"]["NextEvents"]
        self.assertEqual(result["ports"]["state_in"]["bits"], list(range(400, 568)))
        self.assertEqual(result["ports"]["next_out"]["bits"], list(range(400, 568)))
        self.assertFalse(result["cells"])

    def test_changed_initialization_alias_clock_and_domain_are_rejected(self):
        for fault in ("init", "alias", "clock", "missing", "assumption"):
            actual, legacy, original = copy.deepcopy(self.models())
            body = actual["modules"]["BbcCpuEvents"]
            if fault == "init":
                body["netnames"]["field0"]["attributes"]["init"] = "1"
            elif fault == "alias":
                body["netnames"]["field1"]["bits"] = [400]
            elif fault == "clock":
                body["cells"]["field0"]["connections"]["CLK"] = [4]
            elif fault == "missing":
                del body["cells"]["field0"]
            else:
                body["cells"]["hidden"] = {"type": "$assume"}
            with self.subTest(fault=fault), self.assertRaises(ValueError):
                explicit_events.cut(actual, legacy, original)

    def test_native_fault_requires_the_exact_lost_handler_case(self):
        output = "CPU_EVENT_MISMATCH period 1 phase 0 withdraw 0 handlers 1/2\n"
        result = subprocess.CompletedProcess([], 1, output, "")
        self.assertTrue(explicit_events.native_outcome(result, "lost-accepted"))
        for code, text, error in (
            (0, output, ""),
            (2, output, ""),
            (1, output, "setup failure"),
            (1, output.replace("1/2", "0/2"), ""),
            (1, output.replace("phase 0", "phase 1"), ""),
        ):
            with self.subTest(text=text):
                self.assertFalse(
                    explicit_events.native_outcome(
                        subprocess.CompletedProcess([], code, text, error), "lost-accepted"
                    )
                )


if __name__ == "__main__":
    unittest.main()
