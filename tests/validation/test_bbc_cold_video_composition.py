"""Composed evidence must reject stale bytes and incomplete component seals."""

import json
import tempfile
import unittest
from pathlib import Path

from tools.bbc.cold_video_composition import sealed, unchanged
from tools.validation.common import digest


class ColdVideoCompositionTests(unittest.TestCase):
    def test_component_evidence_requires_real_pass_flags_and_artifact_hashes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            artifact, receipt = root / "observations.tsv", root / "result.json"
            artifact.write_text("complete observations\n")
            report = {
                "passed": True,
                "checks": {"complete": True, "equal": True},
                "artifacts": {str(artifact): digest(artifact)},
            }
            receipt.write_text(json.dumps(report))
            self.assertEqual(sealed(receipt), report)
            for changed in (
                {**report, "passed": False},
                {**report, "passed": 1},
                {**report, "checks": {}},
                {**report, "checks": {"complete": True, "equal": False}},
                {**report, "checks": {"complete": 1}},
                {**report, "artifacts": {}},
                {key: value for key, value in report.items() if key != "artifacts"},
            ):
                receipt.write_text(json.dumps(changed))
                with self.subTest(changed=changed), self.assertRaises(ValueError):
                    sealed(receipt)

    def test_a_changed_capture_cannot_enter_a_composition(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            artifact, receipt = root / "capture.bin", root / "result.json"
            artifact.write_bytes(b"captured bytes")
            receipt.write_text(
                json.dumps(
                    {
                        "passed": True,
                        "artifacts": {str(artifact): digest(artifact)},
                    }
                )
            )
            sealed(receipt)
            artifact.write_bytes(b"different bytes")
            with self.assertRaises(ValueError):
                sealed(receipt)

    def test_missing_or_changed_producer_hashes_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "observer.mjs"
            source.write_text("original observer")
            hashes = {str(source): digest(source)}
            unchanged(hashes)
            with self.assertRaises(ValueError):
                unchanged({})
            source.write_text("changed observer")
            with self.assertRaises(ValueError):
                unchanged(hashes)


if __name__ == "__main__":
    unittest.main()
