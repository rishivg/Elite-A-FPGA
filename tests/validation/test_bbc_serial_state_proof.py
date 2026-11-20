"""Temporal serial evidence must reject incomplete or changed support."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.bbc import serial_state_proof
from tools.bbc.serial_state_proof import sealed
from tools.validation.common import ROOT, digest


class SerialStateEvidenceTests(unittest.TestCase):
    def test_support_requires_literal_passes_and_both_source_and_artifact_hashes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            producer, artifact, receipt = (
                root / "producer.v",
                root / "capture.tsv",
                root / "result.json",
            )
            producer.write_text("module original; endmodule\n")
            artifact.write_text("all accepted events\n")
            report = {
                "passed": True,
                "inputs_unchanged": True,
                "inputs": {str(producer): digest(producer)},
                "artifacts": {str(artifact): digest(artifact)},
            }
            receipt.write_text(json.dumps(report))
            self.assertEqual(sealed(receipt), report)
            for field in ("passed", "inputs_unchanged"):
                for value in (False, 1, None):
                    receipt.write_text(json.dumps({**report, field: value}))
                    with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                        sealed(receipt)
            for field in ("inputs", "artifacts"):
                receipt.write_text(json.dumps({**report, field: {}}))
                with self.subTest(field=field), self.assertRaises(ValueError):
                    sealed(receipt)
            receipt.write_text(json.dumps(report))
            for path in (producer, artifact):
                original = path.read_text()
                path.write_text("changed bytes\n")
                with self.subTest(path=path), self.assertRaises(ValueError):
                    sealed(receipt)
                path.write_text(original)


class SerialProofScopeTests(unittest.TestCase):
    def test_component_failure_keeps_existing_parent_proof_untouched(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "build") as temporary:
            root = Path(temporary)
            parent = root / "serial-state-proof"
            parent.mkdir()
            receipt = parent / "result.json"
            receipt.write_text("parent proof must remain unchanged\n")
            source = root / "BbcSerial.v"
            source.write_text("""module BbcSerial(input clk,reset);
reg [7:0] control; reg [2:0] baud; reg rs423; reg empty; reg pending;
reg [18:0] remaining; reg [18:0] transmitPeriod;
wire finishing=(remaining == 19'h00001); wire [7:0] value=8'hfe;
always @(posedge clk) begin
control<=0; baud<=0; rs423<=0; empty<=0; pending<=0;
remaining<=0; transmitPeriod<=0;
end
endmodule
""")
            original = {
                "counts": {"cases": 12928, "observations": 142720},
                "clocks": [{"clock_multiplier": m, "passed": True} for m in (1, 3)],
                "negative_controls": [{"detected": True}, {"detected": True}],
            }

            def support(path):
                self.assertEqual(path, root / "serial-probe-fast/result.json")
                return original

            from types import SimpleNamespace

            failure = SimpleNamespace(returncode=1, stdout="", stderr="proof did fail")
            with (
                patch.object(serial_state_proof.build, "BBC", root),
                patch.object(serial_state_proof.bus_decode, "DIRECTORY", root),
                patch.object(serial_state_proof.bus_decode, "elaborate", return_value={}),
                patch.object(serial_state_proof, "sealed", side_effect=support),
                patch.object(serial_state_proof.subprocess, "run", return_value=failure),
                self.assertRaisesRegex(ValueError, "Serial temporal proof failed"),
            ):
                serial_state_proof.qualify(component_only=True)
            self.assertEqual(receipt.read_text(), "parent proof must remain unchanged\n")
            component = json.loads((root / "serial-component-state-proof/result.json").read_text())
            self.assertFalse(component["passed"])
            self.assertFalse(component["production_bound"])

    def test_default_mode_still_requires_parent_bindings(self):
        with (
            patch.object(serial_state_proof.bus_decode, "elaborate", return_value={}),
            patch.object(
                serial_state_proof, "sealed", side_effect=ValueError("missing parent")
            ) as seal,
            patch.object(serial_state_proof.subprocess, "run") as solver,
        ):
            with self.assertRaisesRegex(ValueError, "missing parent"):
                serial_state_proof.qualify()
            self.assertEqual(seal.call_args.args[0].name, "result.json")
            self.assertEqual(seal.call_args.args[0].parent.name, "bus-binding-proof")
            solver.assert_not_called()


if __name__ == "__main__":
    unittest.main()
