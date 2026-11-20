"""Constant preparation must preserve fresh positive and negative SAT results."""

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from tools.bbc.proofs.cpu_reference import specialized


@unittest.skipUnless(shutil.which("yosys"), "Yosys required for specialization regressions")
class SymbolicSpecializationTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="fpgaleet-symbolic-constants-")
        self.addCleanup(temporary.cleanup)
        self.work = Path(temporary.name)

    def yosys(self, command):
        return subprocess.run(
            ["yosys", "-Q", "-T", "-p", command],
            capture_output=True,
            text=True,
            timeout=30,
        )

    def graph(self, positive=False):
        source, graph = self.work / "source.v", self.work / "graph.json"
        source.write_text(
            "module Property(input clk,output equivalent);\n"
            "(* anyconst *) reg [7:0] firstOpcode;\n"
            "(* anyseq *) reg [7:0] nextOpcode;\n"
            "(* anyseq *) reg trigger;\n"
            "reg [2:0] count=0;always @(posedge clk)count<=count+1;\n"
            "always @* assume(firstOpcode==8'hea && nextOpcode==8'hea);\n"
            f"assign equivalent=count!=3 || trigger || 1'b{int(positive)};\nendmodule\n"
        )
        run = self.yosys(
            f"read_verilog -formal {source}; prep -top Property; chformal -lower; write_json {graph}"
        )
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        return graph

    def test_both_fresh_solver_outcomes_and_initialized_depth_are_preserved(self):
        bindings = {"firstOpcode": 234, "nextOpcode": 234}
        for positive in (True, False):
            with self.subTest(positive=positive):
                graph = self.graph(positive)
                original = json.loads(graph.read_text())
                substituted = specialized.substitute(original, "Property", bindings)
                self.assertEqual(original, json.loads(graph.read_text()))
                fixed, optimized = self.work / "fixed.json", self.work / "optimized.json"
                fixed.write_text(json.dumps(substituted))
                prepare = self.yosys(
                    f"read_json {fixed}; opt -full; check -assert; write_json {optimized}"
                )
                self.assertEqual(prepare.returncode, 0, prepare.stdout + prepare.stderr)
                specialized.check_constants(json.loads(optimized.read_text()), "Property", bindings)
                for path, stimulus in (
                    (graph, "-set firstOpcode 234 -set nextOpcode 234"),
                    (optimized, ""),
                ):
                    run = self.yosys(
                        f"read_json {path}; sat -set-assumes -set-init-zero -seq 5 {stimulus} -prove equivalent 1 -verify"
                    )
                    self.assertEqual(run.returncode == 0, positive, run.stdout + run.stderr)
                    self.assertIn(
                        "SUCCESS!" if positive else "proof did fail!", run.stdout + run.stderr
                    )

    def test_partial_driver_overlap_and_out_of_range_bindings_are_rejected(self):
        graph = json.loads(self.graph().read_text())
        for value in (-1, 256, True):
            with self.subTest(value=value), self.assertRaises(ValueError):
                specialized.substitute(graph, "Property", {"firstOpcode": value})
        module = graph["modules"]["Property"]
        module["netnames"]["alias"] = {**module["netnames"]["firstOpcode"]}
        with self.assertRaisesRegex(ValueError, "overlapping"):
            specialized.substitute(graph, "Property", {"firstOpcode": 234, "alias": 234})
        module["netnames"]["partial"] = {"bits": module["netnames"]["firstOpcode"]["bits"][:4]}
        with self.assertRaisesRegex(ValueError, "whole free"):
            specialized.substitute(graph, "Property", {"partial": 10})

    def test_undefined_constants_fail_the_post_optimization_check(self):
        graph = json.loads(self.graph().read_text())
        bindings = {"firstOpcode": 234}
        fixed = specialized.substitute(graph, "Property", bindings)
        specialized.check_constants(fixed, "Property", bindings)
        fixed["modules"]["Property"]["netnames"]["firstOpcode"]["bits"][0] = "x"
        with self.assertRaisesRegex(ValueError, "undefined"):
            specialized.check_constants(fixed, "Property", bindings)


if __name__ == "__main__":
    unittest.main()
