"""Source SMT checks must preserve initialization and find real failures."""

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from tools.bbc.proofs.cpu_reference import smt


def available():
    try:
        smt.tools()
        return bool(shutil.which("yosys"))
    except ValueError:
        return False


@unittest.skipUnless(available(), "Yosys SMT driver and Z3 required")
class SourceSmtHistoryTest(unittest.TestCase):
    def graph(self, work, *, increment="if(count<3)", initialized=True, seed_init=""):
        rtl, graph = work / "property.v", work / "graph.json"
        rtl.write_text(
            "module Property(input clk,output equivalent);\n"
            f"(* anyconst *) reg seed{seed_init};\n"
            f"reg [2:0] count{'=0' if initialized else ''};\n"
            f"always @(posedge clk){increment} count<=count+1;\n"
            "assign equivalent=count<=3 || seed;\nendmodule\n"
        )
        trial = subprocess.run(
            [
                "yosys",
                "-Q",
                "-T",
                "-p",
                f"read_verilog -formal {rtl}; prep -top Property; opt; write_json {graph}",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(trial.returncode, 0, trial.stdout + trial.stderr)
        return graph

    def test_fresh_bases_induction_and_delayed_counterexample(self):
        with tempfile.TemporaryDirectory(prefix="fpgaleet-source-smt-") as name:
            work = Path(name)
            for positive in (True, False):
                with self.subTest(positive=positive):
                    graph = self.graph(work, increment="if(count<3)" if positive else "")
                    result, artifacts = smt.prove(
                        work / str(positive), graph, "Property", steps=5, timeout=20
                    )
                    self.assertEqual(result["passed"], positive)
                    self.assertEqual(result["register_bits"], 3)
                    self.assertEqual(
                        [c["name"] for c in result["checks"]],
                        ["base", "induction"] if positive else ["base"],
                    )
                    self.assertTrue(all(p.is_file() for p in artifacts))
                    if not positive:
                        self.assertIn(
                            "Status: FAILED", (work / str(positive) / "base.log").read_text()
                        )
                        self.assertTrue((work / str(positive) / "base-witness.vcd").is_file())

    def test_missing_ff_initialization_and_initialized_seed_are_rejected(self):
        with tempfile.TemporaryDirectory(prefix="fpgaleet-source-smt-init-") as name:
            work = Path(name)
            graph = self.graph(work, initialized=False)
            with self.assertRaises(ValueError):
                smt.guard_initialization(json.loads(graph.read_text()), "Property")
            graph = self.graph(work)
            design = json.loads(graph.read_text())
            design["modules"]["Property"]["netnames"]["seed"]["attributes"]["init"] = "0"
            with self.assertRaises(ValueError):
                smt.guard_initialization(design, "Property")


if __name__ == "__main__":
    unittest.main()
