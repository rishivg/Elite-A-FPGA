"""A reload history cannot pass on retained playable RAM without new disc activity."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.bbc import machine_restart


class MachineRestartCoverageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.reference = self.root / "reference"
        self.dut = self.root / "dut"
        self.reference.mkdir()
        self.dut.mkdir()
        for _, name in machine_restart.GAME_POINTS:
            for path in (self.reference, self.dut):
                (path / (name + ".ram")).write_bytes(b"own observed RAM")
        self.points = [
            {"cycle": cycle, "name": name} for cycle, name in machine_restart.GAME_POINTS
        ]
        self.metadata = {"ready": True, "requested": self.points, "observed": self.points}
        self.save()
        (self.dut / "summary.json").write_text(json.dumps({"checkpoints": 2}))
        self.clock = machine_restart.GAME_RESTARTS[0]
        self.mmio = self.reference / "cold-mmio.tsv"
        self.mmio.write_text(f"{self.clock // 16 + 1} 65152 83\n")
        self.patch = patch.object(
            machine_restart,
            "game_state",
            return_value={
                "commander_name": "NEWCOME",
                "credits_tenths": 5000,
                "fuel_tenths_ly": 60,
                "view": 8,
                "docked": True,
            },
        )
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def save(self):
        (self.reference / "cold-checkpoints.json").write_text(json.dumps(self.metadata))

    def test_retained_ram_needs_actual_post_restart_disc_reads(self):
        self.assertTrue(
            all(
                machine_restart.game_coverage(self.reference, self.dut, self.clock)[
                    "checks"
                ].values()
            )
        )
        for rows in ("", f"{self.clock // 16} 65152 83\n", f"{self.clock // 16 + 1} 65153 83\n"):
            self.mmio.write_text(rows)
            checks = machine_restart.game_coverage(self.reference, self.dut, self.clock)["checks"]
            self.assertTrue(checks["playable_before_and_after_restart"])
            self.assertFalse(checks["disc_read_commands_after_restart"])

    def test_missing_original_or_native_checkpoint_is_rejected(self):
        for observed in (self.points[:-1], self.points[::-1], self.points + self.points[:1]):
            self.metadata["observed"] = observed
            self.save()
            self.assertFalse(
                machine_restart.original_game_coverage(self.reference, self.clock)["checks"][
                    "all_cold_checkpoints_observed"
                ]
            )
        self.metadata["observed"] = self.points
        self.metadata["ready"] = 1
        self.save()
        self.assertFalse(
            machine_restart.original_game_coverage(self.reference, self.clock)["checks"][
                "all_cold_checkpoints_observed"
            ]
        )
        (self.dut / "summary.json").write_text(json.dumps({"checkpoints": 1}))
        self.assertFalse(
            machine_restart.game_coverage(self.reference, self.dut, self.clock)["checks"][
                "all_native_checkpoints_observed"
            ]
        )


if __name__ == "__main__":
    unittest.main()
