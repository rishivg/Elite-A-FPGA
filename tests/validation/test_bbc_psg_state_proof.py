import copy
import json
import tempfile
import unittest
from pathlib import Path

from tools.bbc.psg_state_proof import STATE, observed, supporting_evidence
from tools.validation.common import digest


def model():
    declarations = "\n".join(
        "reg " + (f"[{bits - 1}:0] " if bits > 1 else "") + name + ";"
        for name, bits in STATE.items()
    )
    return (
        "module Psg(input clk);\n"
        + declarations
        + "\nalways @(posedge clk) begin\n"
        + "\n".join(name + " <= " + name + ";" for name in STATE)
        + "\nend\nendmodule"
    )


class PsgStateProofGuards(unittest.TestCase):
    def test_every_sequential_bit_is_observed(self):
        actual = model()
        self.assertEqual(sum(STATE.values()), 128)
        self.assertIn("output wire [127:0] proofState", observed(actual))
        for bad in (
            actual.replace("acc <= acc;", "acc <= acc; extra <= 0;"),
            actual.replace("acc <= acc;", "acc <= acc; extra[0] <= 0;"),
            actual.replace("acc <= acc;", ""),
            actual.replace("reg [8:0] acc;", "reg [7:0] acc;"),
            actual.replace("reg noiseFf;", "reg [1:0] noiseFf;"),
        ):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                observed(bad)

    def test_support_requires_complete_original_captures_and_unchanged_bytes(self):
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            artifact = directory / "data"
            artifact.write_bytes(b"declared sound fixture")
            hashes = {str(artifact): digest(artifact)}
            report = {
                "passed": True,
                "coverage_complete": True,
                "sources": hashes,
                "artifacts": hashes,
                "fixture_cycles": 128,
                "captures": {
                    kind: {"passed": True, "different_samples": 0, "samples": 2}
                    for kind in ("levels", "pins")
                },
            }
            path = directory / "result.json"
            path.write_text(json.dumps(report))
            self.assertEqual(supporting_evidence(path), report)
            mutations = [
                {**report, "passed": 1},
                {**report, "coverage_complete": False},
                {**report, "sources": {}},
                {**report, "captures": {"levels": report["captures"]["levels"]}},
            ]
            bad = copy.deepcopy(report)
            bad["captures"]["pins"]["samples"] = 1
            mutations.append(bad)
            for bad in mutations:
                path.write_text(json.dumps(bad))
                with self.subTest(bad=bad), self.assertRaises(ValueError):
                    supporting_evidence(path)
            path.write_text(json.dumps(report))
            artifact.write_bytes(b"changed fixture")
            with self.assertRaises(ValueError):
                supporting_evidence(path)


if __name__ == "__main__":
    unittest.main()
