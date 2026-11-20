"""Prepared boundary rechecks must execute SAT and reject changed artifacts."""

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.bbc.proofs.cpu_reference import recheck_boundaries
from tools.validation.common import digest


@unittest.skipUnless(shutil.which("yosys"), "Yosys required for proof recheck regressions")
class BoundaryRecheckTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="fpgaleet-boundary-recheck-test-")
        self.addCleanup(temporary.cleanup)
        self.work = Path(temporary.name)

    def graph(self, value):
        source = self.work / "property.v"
        graph = self.work / "graph.json"
        source.write_text(
            "module Property(output equivalent);\n"
            "(* anyconst *) reg [7:0] opcode;\n"
            "(* keep *) wire [5:0] step=0;\n"
            "always @* assume(opcode==8'hea);\n"
            f"assign equivalent=1'b{value};\nendmodule\n"
        )
        subprocess.run(
            [
                "yosys",
                "-Q",
                "-T",
                "-p",
                f"read_verilog -formal {source}; prep -top Property; chformal -lower; write_json {graph}",
            ],
            capture_output=True,
            check=True,
            timeout=30,
        )
        return graph

    def test_same_graph_is_rechecked_and_failing_graph_has_a_fresh_witness(self):
        case = {"name": "positive", "graph": self.graph(1), "opcode": 0xEA, "depth": 2}
        positive = recheck_boundaries.execute(case, self.work)
        self.assertTrue(positive["passed"])
        self.assertTrue(positive["sat_executed"])
        # Even a syntactically valid prepared graph of a failing property must
        # be decided by the fresh solver, rather than an earlier success flag.
        case.update(name="negative", graph=self.graph(0))
        negative = recheck_boundaries.execute(case, self.work)
        self.assertFalse(negative["passed"])
        self.assertNotEqual(negative["exit_status"], 0)
        self.assertTrue((self.work / "negative/witness.json").is_file())

    def test_changed_child_graph_is_rejected_before_solver_execution(self):
        root = self.work / "repo"
        original = root / "build/bbc/cpu-c-instruction-bridge/result.json"
        parent = root / "build/bbc/cpu-c-fetch-boundaries/result.json"
        parent.parent.mkdir(parents=True)
        original.parent.mkdir(parents=True)
        graph = self.graph(1)
        child = parent.parent / "ea.json"
        child.write_text(
            json.dumps({"passed": True, "opcode": "ea", "artifacts": {str(graph): digest(graph)}})
        )
        official = [f"{n:02x}" for n in range(151)]
        official[-1] = "ea"
        base = {"opcodes": official, "inputs": {}, "artifacts": {}}
        record = {**base, "complete_official_domain": True}
        graph.write_text("changed prepared graph")
        with (
            patch.object(recheck_boundaries, "ROOT", root),
            patch.object(
                recheck_boundaries.memory_composition, "sealed", side_effect=[base, record]
            ),
            self.assertRaisesRegex(ValueError, "Changed source boundary artifact"),
        ):
            recheck_boundaries.collect(["fetch"], {0xEA})


if __name__ == "__main__":
    unittest.main()
