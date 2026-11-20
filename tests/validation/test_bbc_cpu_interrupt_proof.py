"""Reject changed protocol context bindings or incomplete independent support."""

import unittest
from unittest.mock import patch

from tools.bbc import cpu_interrupt_proof as proof
from tools.bbc.via_timer1_state_proof import initial_reset_assumption


class InterruptProofGuards(unittest.TestCase):
    def core(self):
        states = "BRK1=6'd9,BRK2=6'd10,BRK3=6'd11,JMP0=6'd22,JMP1=6'd23,DECODE=6'd12;"
        scalars = "NMI_edge res NMI_1 bbc_nmi_vector bbc_irq_sample bbc_irq_second bbc_irq_polled bbc_nmi_sample bbc_nmi_second bbc_nmi_polled".split()
        return (
            "module core();\n"
            + states
            + "\nreg [5:0] state;\nreg [15:0] PC_temp;\n"
            + "\n".join("reg " + name + ";" for name in scalars)
            + "\nendmodule\n"
        )

    def wrapper(self):
        return "module wrapper();\nreg warm_pending=0, warm_second=0;\nwire step = enable && reset_dummy == 0;\ncore actual(.BBC_P(flags));\nendmodule\n"

    def test_restart_inactivity_cannot_use_an_unbound_control(self):
        with self.assertRaisesRegex(ValueError, "restart-control binding"):
            proof.observed(self.core(), self.wrapper().replace("warm_pending=0", "warm_pending=1"))

    def test_restart_controls_are_observed_as_proved_invariants(self):
        observed = proof.observed(self.core(), self.wrapper())
        self.assertIn("assign proofRestartControls={warm_pending,warm_second};", observed)
        self.assertNotIn("assume", observed)

    def test_changed_accepted_clock_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "accepted-clock"):
            proof.observed(
                self.core(), self.wrapper().replace("enable && reset_dummy == 0", "enable")
            )

    def test_changed_interrupt_slot_encoding_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "context binding"):
            proof.observed(self.core().replace("BRK1=6'd9", "BRK1=6'd8"), self.wrapper())

    def test_persistent_reset_assumption_cannot_hide_interrupt_inputs(self):
        graph = {
            "modules": {
                "CpuInterruptContract": {
                    "ports": {"reset": {"bits": [2]}},
                    "cells": {
                        "assume": {"type": "$assume", "connections": {"A": [2], "EN": ["1"]}},
                        "initial": {"type": "$initstate", "connections": {"Y": [3]}},
                    },
                }
            }
        }
        with self.assertRaises(ValueError):
            initial_reset_assumption(graph, "CpuInterruptContract")

    def test_missing_fault_domain_cannot_qualify_the_protocol(self):
        support = {
            "cases": 96640,
            "enabled_cycles_per_period": 8697600,
            "negative_controls": [{"name": "old-nmi-poll", "detected": True}],
            "universal_cpu_equivalence": False,
            "universal_game_equivalence": False,
        }
        with (
            patch.object(proof.cpu_instruction_proof, "supporting_evidence", return_value=support),
            self.assertRaisesRegex(ValueError, "Incomplete"),
        ):
            proof.supporting_evidence(None)


if __name__ == "__main__":
    unittest.main()
