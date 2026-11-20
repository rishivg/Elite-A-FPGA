"""Original replay reuse fails closed without skipping receiving DUT work."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.bbc import reference_reuse, storage
from tools.validation.common import digest, write_json


class ReferenceReuseTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "receiving"
        self.workspace = Path(self.temporary.name) / "bundle/workspace"
        self.run = self.workspace / "build/bbc/runs/source"
        self.case = self.run / "initial-status"
        self.capture = self.case / "reference"
        self.capture.mkdir(parents=True)
        (self.root / "destination").mkdir(parents=True)
        self.scenario = {"name": "initial-status", "duration_us": 1000}
        names = [
            "tools/bbc/reference.mjs",
            "build/bbc/disc.ssd",
            "build/bbc/reference.state",
            "build/bbc/reference-cycle/cycle.node",
            "build/validation/vendor/jsbeeb-pinned/src/machine-session.js",
            "tools/bbc/run.py",
        ]
        for name in names:
            for root in (self.root, self.workspace):
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(name.encode())
        self.inputs = {
            "files": {name: digest(self.root / name) for name in names[:2]},
            "node": "fixture-node",
            "options": {"reference_cpu": "cycle"},
        }
        self.config = {
            "scenario": self.scenario,
            "reference_cpu": "cycle",
            "trace_bus": True,
            "compressed_float_audio": True,
            "jsbeeb": str(self.root / "build/validation/vendor/jsbeeb-pinned"),
            "disc": str(self.root / names[1]),
            "load_state": str(self.root / names[2]),
            "cycle_library": str(self.root / names[3]),
            "output": str(self.root / "destination/reference"),
        }
        source_config = {
            key: value.replace(str(self.root), str(self.workspace))
            if isinstance(value, str)
            else value
            for key, value in self.config.items()
        }
        source_config["output"] = str(self.capture)
        write_json(self.case / "reference.json", source_config)
        state = self.workspace / names[2]
        write_json(
            self.run / "boot-snapshots.json",
            {
                "media_profiles": {
                    "released": {
                        "disc": source_config["disc"],
                        "hashes": {str(state): digest(state)},
                        "independent_boot_receipt": {
                            "backends": {
                                "reference": {
                                    "independent_execution": True,
                                    "inputs": self.inputs,
                                    "states": {names[2]: digest(state)},
                                }
                            }
                        },
                    }
                }
            },
        )
        write_json(
            self.workspace.parent / "execution-bundle.json",
            {
                "independent_file_copies": True,
                "files": {name: digest(self.workspace / name) for name in names},
            },
        )
        write_json(self.capture / "summary.json", {"completed": True, "duration_cycles": 2000})
        (self.capture / "audio-digital.u8").write_bytes(bytes(range(256)) * 100)
        (self.capture / "audio-pins.u8").write_bytes(b"pins" * 1000)
        (self.capture / "final.ram").write_bytes(bytes(65536))
        storage.pack(self.capture)
        self.seal()
        self.addCleanup(patch.stopall)
        patch.object(reference_reuse, "ROOT", self.root).start()
        patch.object(reference_reuse.boot_cache, "inputs", return_value=self.inputs).start()
        patch.object(reference_reuse.boot_cache, "valid", return_value=True).start()

    def seal(self):
        write_json(
            self.case / "comparison.json",
            {
                "passed": True,
                "artifact_hashes": {
                    "reference/" + name: value
                    for name, value in reference_reuse.file_hashes(self.capture).items()
                },
            },
        )

    def reuse(self):
        return reference_reuse.reuse(self.run, "initial-status", self.config)

    def test_complete_original_is_copied_with_independent_inodes_and_guard(self):
        result = self.reuse()
        destination = Path(self.config["output"])
        self.assertEqual(reference_reuse.file_hashes(destination), result["observations"])
        self.assertTrue((destination.parent / "reference-reuse.json").is_file())
        self.assertFalse((destination.parent / "dut").exists())
        for name in result["observations"]:
            self.assertNotEqual(
                (destination / name).stat().st_ino, (self.capture / name).stat().st_ino
            )

    def test_changed_schedule_or_profile_is_rejected(self):
        for change in ({"trace_bus": False}, {"scenario": {**self.scenario, "duration_us": 1001}}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                reference_reuse.reuse(self.run, "initial-status", {**self.config, **change})

    def test_changed_reference_state_and_unverified_boot_are_rejected(self):
        with (
            patch.object(reference_reuse.boot_cache, "valid", return_value=False),
            self.assertRaisesRegex(ValueError, "unverified"),
        ):
            self.reuse()
        Path(self.config["load_state"]).write_bytes(b"foreign state")
        with self.assertRaisesRegex(ValueError, "own state differs"):
            self.reuse()

    def test_partial_or_failed_original_case_is_rejected(self):
        write_json(self.case / "comparison.json", {"passed": False})
        with self.assertRaisesRegex(ValueError, "passing case seal"):
            self.reuse()
        write_json(self.capture / "summary.json", {"completed": False, "duration_cycles": 2000})
        self.seal()
        with self.assertRaisesRegex(ValueError, "did not complete"):
            self.reuse()

    def test_unsealed_change_and_damaged_encoded_tail_are_rejected(self):
        path = self.capture / "audio-pins.u8.gz"
        original = path.read_bytes()
        path.write_bytes(original[:-3])
        with self.assertRaisesRegex(ValueError, "passing case seal"):
            self.reuse()
        self.seal()
        with self.assertRaisesRegex(ValueError, "compressed capture"):
            self.reuse()

    def test_changed_original_producer_is_rejected(self):
        (self.workspace / "tools/bbc/reference.mjs").write_bytes(b"modified original")
        with self.assertRaisesRegex(ValueError, "producer input changed"):
            self.reuse()

    def test_changed_frozen_replay_controller_is_rejected(self):
        (self.workspace / "tools/bbc/run.py").write_bytes(b"unsealed controller")
        with self.assertRaisesRegex(ValueError, "replay controller changed"):
            self.reuse()

    def test_extra_emulator_or_dependency_file_is_rejected(self):
        for root in (self.workspace, self.root):
            with self.subTest(root=root):
                extra = root / "build/validation/vendor/jsbeeb-pinned/extra.js"
                extra.write_bytes(b"unexpected dependency")
                with self.assertRaisesRegex(ValueError, "inventory changed"):
                    self.reuse()
                extra.unlink()

    def test_source_changed_during_copy_never_receives_reuse_seal(self):
        original_copy = reference_reuse.shutil.copy2

        def racing_copy(source, destination):
            result = original_copy(source, destination)
            (self.case / "comparison.json").write_text("{}")
            return result

        with (
            patch.object(reference_reuse.shutil, "copy2", side_effect=racing_copy),
            self.assertRaisesRegex(ValueError, "changed during reuse"),
        ):
            self.reuse()
        directory = Path(self.config["output"]).parent
        self.assertFalse((directory / "reference-reuse.json").exists())
        self.assertTrue((directory / "incomplete-reference-reuse.json").is_file())


if __name__ == "__main__":
    unittest.main()
