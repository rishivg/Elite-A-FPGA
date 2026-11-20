"""The ADC contract must observe actual state and current parent evidence."""

import json
import tempfile
import unittest
from pathlib import Path

from tools.bbc.adc_state_proof import clock_evidence, observed_module, parent_binding
from tools.validation.common import digest

ADC = """module Adc7002(input clk, reset);
reg [3:0] control;
reg [3:0] resultControl;
reg [13:0] conversion;
reg busy;
reg completed;
reg [15:0] sample;
always @(posedge clk) sample <= 0;
endmodule
"""
PARENT = """module Machine(input clk, reset);
Adc7002 adc (
.clk(clk), .reset(reset), .io_channels(io_analogue[63:0]), .io_tick(tickVia)
);
endmodule
"""


class AdcStateProofTests(unittest.TestCase):
    def test_observation_adds_only_ports_and_assignments(self):
        observed = observed_module(ADC)
        self.assertIn("always @(posedge clk) sample <= 0;", observed)
        self.assertIn("assign proofRemaining = conversion;", observed)
        for state in ("control", "resultControl", "conversion", "busy", "completed", "sample"):
            with self.subTest(state=state), self.assertRaises(ValueError):
                observed_module(ADC.replace(state + ";", "other;", 1))
        with self.assertRaises(ValueError):
            observed_module(ADC + ADC)

    def test_wrong_parent_channels_clock_reset_tick_or_missing_child_fails(self):
        self.assertEqual(parent_binding(PARENT, "Machine")["io_tick"], "tickVia")
        for broken in (
            PARENT.replace(".clk(clk)", ".clk(other)"),
            PARENT.replace(".reset(reset)", ".reset(other)"),
            PARENT.replace("io_analogue[63:0]", "other[63:0]"),
            PARENT.replace(".io_tick(tickVia)", ".io_tick(tickCpu)"),
            PARENT.replace("Adc7002 adc", "Adc7002 other"),
        ):
            with self.subTest(broken=broken), self.assertRaises(ValueError):
                parent_binding(broken, "Machine")

    def test_unqualified_or_changed_clock_receipts_cannot_bind_the_timer(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            source, artifact, receipt = (
                directory / name for name in ("rtl.v", "proof.log", "result.json")
            )
            source.write_text("actual RTL")
            artifact.write_text("proof")
            report = {
                "passed": True,
                "inputs_unchanged": True,
                "negative_control_detected": True,
                "clock_multiplier": 3,
                "inputs": {str(source): digest(source)},
                "artifacts": {str(artifact): digest(artifact)},
            }
            receipt.write_text(json.dumps(report))
            self.assertEqual(clock_evidence(receipt, 3), report)
            for key, value in (
                ("passed", False),
                ("passed", 1),
                ("inputs_unchanged", False),
                ("negative_control_detected", False),
                ("clock_multiplier", 1),
                ("inputs", {}),
                ("artifacts", {}),
            ):
                receipt.write_text(json.dumps({**report, key: value}))
                with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                    clock_evidence(receipt, 3)
            receipt.write_text(json.dumps(report))
            for path in (source, artifact):
                original = path.read_text()
                path.write_text("changed")
                with self.subTest(path=path), self.assertRaises(ValueError):
                    clock_evidence(receipt, 3)
                path.write_text(original)


if __name__ == "__main__":
    unittest.main()
