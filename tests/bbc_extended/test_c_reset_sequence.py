"""An undefined earlier C tick must never qualify a reset-history fault."""

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from tools.bbc.proofs.cpu_reference import reset_sequence


@unittest.skipUnless(shutil.which("yosys"), "Yosys required for reset-history regressions")
class ResetDefinedHistoryTest(unittest.TestCase):
    def test_initial_trap_masks_later_observation_failures(self):
        with tempfile.TemporaryDirectory(prefix="fpgaleet-reset-history-guard-") as name:
            work = Path(name)
            source = work / "source.v"
            # This deliberately invalid source traps on reset dispatch, then
            # emits defined but incorrect later states. Those later mismatches
            # must not be accepted as an actual, defined-history counterexample.
            source.write_text("""module M6502ReferenceNext(input [255:0] state_in,
input [63:0] pins_in,output [255:0] state_out,output [63:0] pins_out,output trap);
assign state_out=0;
assign pins_out=pins_in & ~64'h42000000;
assign trap=pins_in[25] && pins_in[30];
endmodule
""")
            for label, contract in (
                ("guarded", reset_sequence.SEQUENCE),
                (
                    "lost-history-guard",
                    reset_sequence.SEQUENCE.replace(
                        "assign faultDefined=!typedBoolean || !definedHistory || (phase<7 && trap) || equivalent;",
                        "assign faultDefined=!typedBoolean || (phase<7 && trap) || equivalent;",
                    ),
                ),
            ):
                with self.subTest(label=label):
                    property_file = work / (label + ".v")
                    property_file.write_text(contract)
                    graph = work / (label + ".json")
                    preparation = subprocess.run(
                        [
                            "yosys",
                            "-Q",
                            "-T",
                            "-p",
                            f"read_verilog -formal -sv {source} {property_file}; prep -top SourceResetSequence -flatten; opt; write_json {graph}",
                        ],
                        capture_output=True,
                        text=True,
                        timeout=30,
                    )
                    self.assertEqual(preparation.returncode, 0, preparation.stderr)
                    accepted = reset_sequence.run_query(
                        graph,
                        "SourceResetSequence",
                        work / (label + ".log"),
                        work / (label + "-witness.json"),
                        "faultDefined",
                        sequence=8,
                        positive=True,
                    )
                    self.assertEqual(accepted, label == "guarded")


if __name__ == "__main__":
    unittest.main()
