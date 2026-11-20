import json
import tempfile
import unittest
from pathlib import Path

from tools.bbc.cpu_instruction_check import ensure_component
from tools.validation.common import digest


class CpuInstructionCheckDispatch(unittest.TestCase):
    def test_unchanged_component_does_not_execute_again(self):
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            artifact = directory / "capture"
            artifact.write_bytes(b"independent CPU evidence")
            report = directory / "result.json"
            self.seal(report, artifact)
            self.assertEqual(
                ensure_component(report, lambda: self.fail("Unchanged evidence was rerun")),
                "verified-unchanged",
            )

    def test_missing_or_changed_evidence_must_execute_and_validate(self):
        for state in ("missing", "changed", "failed-refresh"):
            with self.subTest(state=state), tempfile.TemporaryDirectory() as name:
                directory = Path(name)
                artifact, report = directory / "capture", directory / "result.json"
                artifact.write_bytes(b"independent CPU evidence")
                if state != "missing":
                    self.seal(report, artifact)
                    artifact.write_bytes(b"changed producer or observation")
                calls = []

                def execute():
                    calls.append("executed")
                    self.seal(report, artifact, passed=state != "failed-refresh")

                if state == "failed-refresh":
                    with self.assertRaises(ValueError):
                        ensure_component(report, execute)
                else:
                    self.assertEqual(ensure_component(report, execute), "refreshed")
                self.assertEqual(calls, ["executed"])

    @staticmethod
    def seal(report, artifact, passed=True):
        hashes = {str(artifact): digest(artifact)}
        report.write_text(
            json.dumps(
                {
                    "passed": passed,
                    "inputs_unchanged": True,
                    "inputs": hashes,
                    "artifacts": hashes,
                    "periods": [{"period": p, "passed": True} for p in (1, 16, 48)],
                }
            )
        )


if __name__ == "__main__":
    unittest.main()
