"""Preservation miters keep every port and prove their reachability invariants."""

import unittest

from tools.bbc.system_pins_proof import miter

GOLDEN = """module BbcSystemPinsBaseline (
 input wire [7:0] io_busPortB,
 output wire [7:0] io_latch,
 output wire io_soundEnabled,
 input wire clk,
 input wire reset
);
reg [7:0] previousPortB;
assign io_latch = 0;
assign io_soundEnabled = 0;
endmodule
"""
CANDIDATE = GOLDEN.replace("BbcSystemPinsBaseline", "BbcSystemPinsCandidate").replace(
    "reg [7:0] previousPortB", "reg [3:0] previousPortB"
)


class SystemPinsProofTests(unittest.TestCase):
    def test_all_outputs_and_both_invariants_are_in_the_miter(self):
        text, outputs = miter(GOLDEN, CANDIDATE)
        self.assertEqual(
            [name for _, name in outputs], ["io_latch", "io_soundEnabled", "proofPrevious"]
        )
        for _, name in outputs:
            self.assertIn("(g_" + name + " == c_" + name + ")", text)
        self.assertIn("assign goldenInvariant", text)
        self.assertIn("assign candidateInvariant", text)
        self.assertEqual(text.count("assign proofPrevious = previousPortB[3:0]"), 2)
        self.assertIn("reg [7:0] previousPortB;", text)
        self.assertIn("reg [3:0] previousPortB;", text)

    def test_missing_changed_or_unobservable_latch_interface_is_rejected(self):
        for candidate in (
            CANDIDATE.replace(" output wire io_soundEnabled,\n", ""),
            CANDIDATE.replace("input wire [7:0] io_busPortB", "input wire [3:0] io_busPortB"),
            CANDIDATE.replace("io_latch", "hidden_latch"),
        ):
            with self.subTest(candidate=candidate), self.assertRaises(ValueError):
                miter(GOLDEN, candidate)


if __name__ == "__main__":
    unittest.main()
