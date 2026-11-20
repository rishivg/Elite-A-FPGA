"""A preparation cache must never hide a changed property or graph."""

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from tools.bbc.proofs.cpu_reference import prepared


@unittest.skipUnless(shutil.which("yosys"), "Yosys required for proof-cache regressions")
class PreparedProofCacheTest(unittest.TestCase):
    def setUp(self):
        self.workspace = tempfile.TemporaryDirectory(prefix="fpgaleet-proof-cache-")
        self.addCleanup(self.workspace.cleanup)
        self.directory = Path(self.workspace.name)
        self.source = self.directory / "source.v"
        self.graph = self.directory / "graph.json"
        self.source.write_text(
            "module Property(output equivalent);assign equivalent=1'b1;endmodule\n"
        )
        self.command = f"read_verilog -formal {self.source}; prep -top Property; check -assert; write_json {self.graph}"

    def prepare(self):
        return prepared.prepare(
            self.directory, self.command, [self.source], [self.graph], timeout=30
        )

    def prove(self):
        return subprocess.run(
            ["yosys", "-Q", "-T", "-p", f"read_json {self.graph}; sat -prove equivalent 1 -verify"],
            capture_output=True,
            text=True,
            timeout=30,
        )

    def test_source_change_exposes_new_counterexample(self):
        self.assertFalse(self.prepare())
        self.assertEqual(self.prove().returncode, 0)
        self.assertTrue(self.prepare())
        self.source.write_text(
            "module Property(output equivalent);assign equivalent=1'b0;endmodule\n"
        )
        self.assertFalse(self.prepare())
        changed = self.prove()
        self.assertNotEqual(changed.returncode, 0)
        self.assertIn("proof did fail!", changed.stderr)

    def test_changed_graph_is_rebuilt(self):
        self.assertFalse(self.prepare())
        # Replace an accepted graph with the valid graph of a failing property.
        negative = self.directory / "negative.v"
        negative.write_text("module Property(output equivalent);assign equivalent=1'b0;endmodule\n")
        subprocess.run(
            [
                "yosys",
                "-Q",
                "-T",
                "-p",
                f"read_verilog {negative}; prep -top Property; write_json {self.graph}",
            ],
            check=True,
            capture_output=True,
            timeout=30,
        )
        self.assertNotEqual(self.prove().returncode, 0)
        self.assertFalse(self.prepare())
        self.assertEqual(self.prove().returncode, 0)


if __name__ == "__main__":
    unittest.main()
