"""Reject generalized-entry proofs that silently narrow interrupt seeds."""

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from tools.bbc.proofs.cpu_reference.fetch_seed_domain import guard_seeds


@unittest.skipUnless(shutil.which("yosys"), "Yosys required for seed-domain regressions")
class FetchSeedDomainTest(unittest.TestCase):
    def test_free_seeds_and_rejected_constraints(self):
        source = """module BbcCpuInterruptStreamContract(output [6:0] seeds);
(* anyconst *) reg [7:0] firstOpcode;
(* anyseq *) reg [7:0] nextOpcode;
(* anyconst *) reg seedNmi;
(* anyconst *) reg seedPrevious;
(* anyconst *) reg [4:0] seedOlderIrq;
always @* assume(firstOpcode==0 && nextOpcode==0);
assign seeds={seedOlderIrq,seedPrevious,seedNmi};
endmodule
"""
        cases = (
            ("free", source),
            ("constant", source.replace("(* anyconst *) reg seedNmi;", "wire seedNmi=0;")),
            ("initialized", source.replace("reg seedNmi;", "reg seedNmi=0;")),
            (
                "aliased",
                source.replace("(* anyconst *) reg seedPrevious;", "wire seedPrevious=seedNmi;"),
            ),
            (
                "assumed",
                source.replace("endmodule", "always @* assume(seedNmi);\nendmodule"),
            ),
        )
        with tempfile.TemporaryDirectory(prefix="fpgaleet-seed-domain-") as name:
            work = Path(name)
            for label, text in cases:
                with self.subTest(label=label):
                    rtl, graph = work / (label + ".v"), work / (label + ".json")
                    rtl.write_text(text)
                    trial = subprocess.run(
                        [
                            "yosys",
                            "-Q",
                            "-T",
                            "-p",
                            f"read_verilog -formal {rtl}; prep -top BbcCpuInterruptStreamContract; chformal -lower; write_json {graph}",
                        ],
                        capture_output=True,
                        text=True,
                        timeout=30,
                    )
                    self.assertEqual(trial.returncode, 0, trial.stdout + trial.stderr)
                    design = json.loads(graph.read_text())
                    if label == "free":
                        self.assertEqual(
                            guard_seeds(design), {"seed_bits": 7, "seed_combinations": 128}
                        )
                    else:
                        with self.assertRaises(ValueError):
                            guard_seeds(design)


if __name__ == "__main__":
    unittest.main()
