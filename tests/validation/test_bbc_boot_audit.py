"""A boot audit must reject changed provenance and incomplete observations."""

import gzip
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.bbc import boot_audit
from tools.validation.common import ROOT, digest, write_json


class BootAuditTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir=ROOT / "build")
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.workspace = self.directory / "frozen"
        self.run = self.workspace / "build/bbc/runs/boot"
        self.run.mkdir(parents=True)
        entry = self.workspace / "tools/bbc/reference.mjs"
        entry.parent.mkdir(parents=True)
        entry.write_text("unchanged original entry")
        state = self.workspace / "build/bbc/own.state"
        state.write_bytes(b"independently executed state")
        self.manifest = {"master_clock_hz": 96000000, "inputs": {}}
        self.manifest["inputs"][str(entry.relative_to(self.workspace))] = digest(entry)
        self.snapshots = {
            "hashes": {str(state): digest(state)},
            "independent_boot_receipt": {
                "backends": {
                    backend: {"independent_execution": True} for backend in ("dut", "reference")
                }
            },
        }
        write_json(self.run / "manifest.json", self.manifest)
        write_json(self.run / "boot-snapshots.json", self.snapshots)
        write_json(self.run / "boot-reference.json", {"power_profile": "shared-reset"})
        dut = self.run / "boot-dut"
        dut.mkdir()
        (dut / "frames.tsv").write_text("512\n640\n")
        (dut / "psg.tsv").write_text("480 127\n")
        (dut / "boot.ram").write_bytes(bytes(65536))
        (dut / "audio.u8").write_bytes(bytes(8))
        write_json(
            dut / "summary.json",
            {
                "completed": True,
                "restored_own_boot_cycles": 0,
                "duration_cycles": 768,
                "frames": 2,
                "psg_writes": 1,
            },
        )
        self.reference_audio = bytes(8)
        self.reference_psg = "30 127\n"
        self.after_reference = lambda: None
        self.addCleanup(patch.stopall)
        patch.object(boot_audit.build, "BBC", self.directory).start()
        self.execute = patch.object(
            boot_audit.subprocess, "run", side_effect=self.original_execution
        ).start()
        patch.object(boot_audit.subprocess, "check_output", return_value="test-node\n").start()

    def original_execution(self, command, **kwargs):
        if command[0] == "node":
            config = json.loads(Path(command[-1]).read_text())
            output = Path(config["output"])
            output.mkdir()
            (output / "psg.tsv").write_text(self.reference_psg)
            (output / "boot.ram").write_bytes(bytes(65536))
            (output / "audio.u8.gz").write_bytes(gzip.compress(self.reference_audio))
            write_json(
                output / "raw-vsync.json",
                {
                    "transitions": [{"cycle": 32, "level": True}, {"cycle": 40, "level": True}],
                    "final_cycle": 48,
                    "observed_audio": True,
                },
            )
            Path(config["save_state"]).write_bytes(b"new reference's own state")
            self.after_reference()
        return subprocess.CompletedProcess(command, 0)

    def test_complete_equal_observations_pass(self):
        report = boot_audit.audit(self.run)
        self.assertTrue(report["passed"])
        self.assertTrue(report["captures_complete"])

    def test_changed_frozen_source_is_rejected_before_execution(self):
        (self.workspace / "tools/bbc/reference.mjs").write_text("changed")
        with self.assertRaisesRegex(ValueError, "asset changed"):
            boot_audit.audit(self.run)
        self.execute.assert_not_called()

    def test_asset_outside_frozen_workspace_is_rejected(self):
        outside = self.directory / "external"
        outside.write_bytes(b"same digest is insufficient")
        self.manifest["inputs"] = {str(outside): digest(outside)}
        write_json(self.run / "manifest.json", self.manifest)
        with self.assertRaisesRegex(ValueError, "asset changed"):
            boot_audit.audit(self.run)
        self.execute.assert_not_called()

    def test_changed_own_boot_is_rejected_before_execution(self):
        Path(next(iter(self.snapshots["hashes"]))).write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "Sealed own boot changed"):
            boot_audit.audit(self.run)
        self.execute.assert_not_called()

    def test_missing_independent_execution_receipt_is_rejected(self):
        self.snapshots["independent_boot_receipt"]["backends"]["reference"] = {}
        write_json(self.run / "boot-snapshots.json", self.snapshots)
        with self.assertRaisesRegex(ValueError, "independent execution receipts"):
            boot_audit.audit(self.run)
        self.execute.assert_not_called()

    def test_mutated_input_during_execution_cannot_pass(self):
        self.after_reference = lambda: (self.run / "boot-dut/audio.u8").write_bytes(bytes([1]) * 8)
        report = boot_audit.audit(self.run)
        self.assertFalse(report["passed"])
        self.assertFalse(report["inputs_unchanged"])

    def test_matching_truncated_audio_is_not_a_complete_capture(self):
        self.reference_audio = bytes(7)
        (self.run / "boot-dut/audio.u8").write_bytes(bytes(7))
        report = boot_audit.audit(self.run)
        self.assertTrue(report["digital_waveform"]["passed"])
        self.assertFalse(report["captures_complete"])
        self.assertFalse(report["passed"])

    def test_missing_audio_tail_counts_as_a_difference(self):
        self.reference_audio = bytes(7)
        report = boot_audit.audit(self.run)
        self.assertEqual(report["digital_waveform"]["different_samples"], 1)
        self.assertFalse(report["passed"])

    def test_missing_sound_event_is_reported_at_the_tail(self):
        self.reference_psg = ""
        report = boot_audit.audit(self.run)
        self.assertEqual(report["sound_events"]["first_difference"], 0)
        self.assertIsNone(report["sound_events"]["expected"])
        self.assertEqual(report["sound_events"]["actual"], (480, 127))
        self.assertFalse(report["passed"])

    def test_one_master_clock_sound_difference_cannot_pass(self):
        (self.run / "boot-dut/psg.tsv").write_text("481 127\n")
        report = boot_audit.audit(self.run)
        self.assertTrue(report["digital_waveform"]["passed"])
        self.assertFalse(report["sound_events"]["passed"])
        self.assertFalse(report["passed"])


if __name__ == "__main__":
    unittest.main()
