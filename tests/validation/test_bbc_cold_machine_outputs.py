"""A live whole-machine claim requires complete, unchanged source evidence."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.bbc import cold_machine_outputs
from tools.validation.common import digest


class WholeMachineEvidenceTests(unittest.TestCase):
    def reject(self, receipt, expression):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "result.json"
            path.write_text(json.dumps(receipt))
            with self.assertRaisesRegex(ValueError, expression):
                cold_machine_outputs.qualify(path, Path(directory) / "absent-mmio.json")

    def test_failed_or_universal_video_claim_cannot_qualify(self):
        for passed, universal in ((False, False), (True, True), (1, False)):
            self.reject(
                {"passed": passed, "universal_game_equivalence": universal},
                "complete independently executed",
            )

    def test_missing_source_or_observation_inventory_cannot_qualify(self):
        self.reject(
            {"passed": True, "universal_game_equivalence": False},
            "inputs or observations changed",
        )

    def test_changed_source_or_observation_cannot_qualify(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, observation = root / "source.v", root / "capture.bin"
            source.write_text("original RTL")
            observation.write_bytes(b"complete observations")
            receipt = {
                "passed": True,
                "universal_game_equivalence": False,
                "inputs": {source.name: digest(source)},
                "artifacts": {observation.name: digest(observation)},
            }
            for target in (source, observation):
                with self.subTest(target=target.name):
                    before = target.read_bytes()
                    target.write_bytes(before + b"changed")
                    with patch.object(cold_machine_outputs, "ROOT", root):
                        self.reject(receipt, "inputs or observations changed")
                    target.write_bytes(before)


if __name__ == "__main__":
    unittest.main()
