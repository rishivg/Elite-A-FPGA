import json
import tempfile
import unittest
from pathlib import Path

from tools.bbc.sound_bus_state_proof import observed, supporting_evidence
from tools.validation.common import digest

MODEL = """module BbcSoundBus(input clk);
reg [7:0] previousData;
reg previousEnabled;
reg [7:0] delay;
always @(posedge clk) begin
previousData<=0;previousEnabled<=0;delay<=0;
end
endmodule
"""


class SoundBusStateProofGuards(unittest.TestCase):
    def test_inventory_rejects_unobserved_state_or_changed_widths(self):
        self.assertIn("output wire [16:0] proofState", observed(MODEL))
        for bad in (
            MODEL.replace("delay<=0;", "delay<=0;extra[0]<=0;"),
            MODEL.replace("previousEnabled<=0;", ""),
            MODEL.replace("reg [7:0] delay;", "reg [8:0] delay;"),
        ):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                observed(bad)

    def test_original_capture_hashes_counts_and_clock_profile_are_required(self):
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            captures = {}
            for kind, suffix in (("levels", ""), ("pins", ".pins"), ("writes", ".writes")):
                content = b"16\t127\n" * 27 if kind == "writes" else bytes(8000)
                capture = {"passed": True}
                for backend in ("reference", "dut"):
                    path = directory / (backend + ".u8" + suffix)
                    path.write_bytes(content)
                    capture[backend + "_sha256"] = digest(path)
                capture.update(
                    {"events": 27}
                    if kind == "writes"
                    else {"samples": 8000, "different_samples": 0}
                )
                captures[kind] = capture
            producer = directory / "producer"
            producer.write_text("independent fixture")
            report = {
                "passed": True,
                "master_clock_hz": 32000000,
                "artifacts": {str(producer): digest(producer)},
                "captures": captures,
            }
            path = directory / "result.json"
            path.write_text(json.dumps(report))
            self.assertEqual(supporting_evidence(path, 1), report)
            with self.assertRaises(ValueError):
                supporting_evidence(path, 3)
            report["passed"] = 1
            path.write_text(json.dumps(report))
            with self.assertRaises(ValueError):
                supporting_evidence(path, 1)
            report["passed"] = True
            for backend in ("reference", "dut"):
                observation = directory / (backend + ".u8")
                observation.write_bytes(bytes(7999))
                report["captures"]["levels"][backend + "_sha256"] = digest(observation)
            path.write_text(json.dumps(report))
            with self.assertRaises(ValueError):
                supporting_evidence(path, 1)


if __name__ == "__main__":
    unittest.main()
