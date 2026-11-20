"""Own boots may cross workspaces only with unchanged backend inputs and bytes."""

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.bbc import boot_cache, boot_reuse, build
from tools.validation.common import ROOT, digest, write_json


class BootReuseTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir=ROOT / "build")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "receiver"
        self.bbc = self.root / "build/bbc"
        self.bbc.mkdir(parents=True)
        self.bundle = self.bbc / "execution-original"
        self.workspace = self.bundle / "workspace"
        source = self.workspace / "build/bbc"
        source.mkdir(parents=True)
        self.names = {
            "dut": ["boot.state", "boot.state.fm", "boot.state.ssd", "boot.state.rgb"],
            "reference": ["reference-cycle-clocked-fm-shared-reset-bbc-result-bits.state"],
        }
        self.addCleanup(patch.stopall)
        for module in [boot_reuse, boot_cache]:
            patch.object(module, "ROOT", self.root).start()
        for module in [boot_cache, build]:
            patch.object(module, "BBC", self.bbc).start()
        patch.object(boot_cache, "RECEIPT", self.bbc / "boot-cache.json").start()
        patch.object(
            boot_cache,
            "BACKENDS",
            {backend: [self.bbc / name for name in names] for backend, names in self.names.items()},
        ).start()
        self.inputs = {backend: {"model": backend, "profile": "declared"} for backend in self.names}
        patch.object(boot_cache, "inputs", side_effect=lambda backend: self.inputs[backend]).start()
        self.native = patch.object(build, "native_current", return_value=True).start()
        patch.object(boot_cache, "native_current", return_value=True).start()
        receipt = {"schema": 2, "backends": {}}
        for backend, names in self.names.items():
            states = {}
            for name in names:
                (source / name).write_bytes((backend + " original own " + name).encode())
                (self.bbc / name).write_bytes(("prior " + name).encode())
                states["build/bbc/" + name] = digest(source / name)
            receipt["backends"][backend] = {
                "inputs": self.inputs[backend],
                "states": states,
                "independent_execution": "This backend executed its own boot",
                "source_run": str(source / "runs/original"),
            }
        self.receipt = receipt
        write_json(source / "boot-cache.json", receipt)
        write_json(self.bbc / "boot-cache.json", {"schema": 2, "backends": {}})
        write_json(self.bundle / "execution-bundle.json", {"independent_file_copies": True})
        write_json(
            self.bundle / "execution-result.json",
            {
                "bundle_manifest": digest(self.bundle / "execution-bundle.json"),
                "exit_code": 1,
            },
        )
        latest = source / "latest"
        latest.mkdir()
        write_json(
            latest / "report.json", {"inputs_unchanged": True, "boot_provenance_complete": True}
        )
        write_json(
            latest / "boot-snapshots.json",
            {
                "independent_boot_receipt": receipt,
                "hashes": {
                    str(source / name): digest(source / name)
                    for names in self.names.values()
                    for name in names
                },
            },
        )
        self.before = {
            name: (self.bbc / name).read_bytes() for names in self.names.values() for name in names
        }

    def assert_unchanged(self):
        for name, expected in self.before.items():
            self.assertEqual((self.bbc / name).read_bytes(), expected)

    def test_exact_own_boots_become_current_and_previous_bytes_are_retained(self):
        result = boot_reuse.reuse(self.bundle)
        self.assertEqual(result["reused"], ["dut", "reference"])
        self.assertTrue(boot_cache.valid())
        history = Path(result["retained_previous"])
        for backend, names in self.names.items():
            for name in names:
                source, target = self.workspace / "build/bbc" / name, self.bbc / name
                self.assertEqual(target.read_bytes(), source.read_bytes())
                self.assertNotEqual(target.stat().st_ino, source.stat().st_ino)
                self.assertEqual((history / "build/bbc" / name).read_bytes(), self.before[name])
        self.assertEqual(json.loads((self.bbc / "boot-cache.json").read_text()), self.receipt)
        self.assertEqual(
            boot_reuse.reuse(self.bundle), {"reused": [], "existing_boots_current": True}
        )

    def test_changed_source_state_or_missing_sidecar_rejects_before_publication(self):
        for name in ["boot.state", "boot.state.ssd"]:
            source = self.workspace / "build/bbc" / name
            original = source.read_bytes()
            source.unlink()
            with self.assertRaises(ValueError):
                boot_reuse.reuse(self.bundle)
            self.assert_unchanged()
            source.write_bytes(original + b"changed")
            with self.assertRaises(ValueError):
                boot_reuse.reuse(self.bundle)
            self.assert_unchanged()
            source.write_bytes(original)

    def test_changed_backend_inputs_and_stale_native_reject(self):
        for backend in self.names:
            original = self.inputs[backend]
            self.inputs[backend] = {**original, "profile": "changed"}
            with self.assertRaises(ValueError):
                boot_reuse.reuse(self.bundle)
            self.assert_unchanged()
            self.inputs[backend] = original
        self.native.return_value = False
        with self.assertRaises(ValueError):
            boot_reuse.reuse(self.bundle)
        self.assert_unchanged()

    def test_32_mhz_bundle_cannot_supply_96_mhz_boots(self):
        with self.assertRaises((ValueError, FileNotFoundError)):
            boot_reuse.reuse(self.bundle, clock_multiplier=3)
        self.assert_unchanged()

    def test_selecting_dut_never_imports_another_reference_profile(self):
        self.inputs["reference"] = {"model": "reference", "profile": "different"}
        result = boot_reuse.reuse(self.bundle, backend="dut")
        self.assertEqual(result["reused"], ["dut"])
        self.assertTrue(boot_cache.valid("dut"))
        self.assertFalse(boot_cache.valid("reference"))
        for name in self.names["reference"]:
            self.assertEqual((self.bbc / name).read_bytes(), self.before[name])

    def test_media_own_boots_use_their_snapshot_and_leave_released_boots_intact(self):
        source = self.workspace / "build/bbc"
        disc = self.bbc / "input-media/rich.ssd"
        disc.parent.mkdir()
        disc.write_bytes(b"declared medium")
        prefix = self.bbc / "boots/medium"
        context = boot_cache.Profile(
            disc=disc,
            backends={
                "dut": [
                    prefix / "dut.state",
                    prefix / "dut.state.fm",
                    prefix / "dut.state.ssd",
                    prefix / "dut.state.rgb",
                ],
                "reference": [prefix / "reference.state"],
            },
            receipt=prefix / "boot-cache.json",
            reference_options=dict(boot_cache.REFERENCE_OPTIONS),
        )
        hashes = {}
        for backend, paths in context.backends.items():
            states = {}
            for path in paths:
                name = path.relative_to(self.root)
                original = self.workspace / name
                original.parent.mkdir(parents=True, exist_ok=True)
                original.write_bytes((backend + " own medium state " + str(name)).encode())
                states[str(name)] = digest(original)
                hashes[str(original)] = digest(original)
            self.receipt["backends"][backend]["states"] = states
        write_json(self.workspace / context.receipt.relative_to(self.root), self.receipt)
        write_json(
            source / "latest/boot-snapshots.json",
            {
                "media_profiles": {
                    "rich": {
                        "disc": str(self.workspace / disc.relative_to(self.root)),
                        "independent_boot_receipt": self.receipt,
                        "hashes": hashes,
                    }
                }
            },
        )
        with patch.object(
            boot_cache, "inputs", side_effect=lambda backend, context=None: self.inputs[backend]
        ):
            result = boot_reuse.reuse(self.bundle, context=context)
            self.assertEqual(result["reused"], ["dut", "reference"])
            self.assertTrue(boot_cache.valid(context=context))
            for paths in context.backends.values():
                for path in paths:
                    self.assertEqual(
                        path.read_bytes(),
                        (self.workspace / path.relative_to(self.root)).read_bytes(),
                    )
            self.assert_unchanged()
            wrong_clock = boot_cache.Profile(
                disc, context.backends, context.receipt, context.reference_options, 3
            )
            with self.assertRaises(ValueError):
                boot_reuse.reuse(self.bundle, context=wrong_clock)
            self.assert_unchanged()

    def test_exact_96_mhz_own_boots_remain_in_their_clock_profile(self):
        source = self.workspace / "build/bbc"
        self.names["dut"] = ["full-video/" + name for name in self.names["dut"]]
        for backend, names in self.names.items():
            states = {}
            for name in names:
                path = source / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes((backend + " original own " + name).encode())
                states["build/bbc/" + name] = digest(path)
            self.receipt["backends"][backend]["states"] = states
        write_json(source / "full-video/boot-cache.json", self.receipt)
        write_json(
            source / "latest/boot-snapshots.json",
            {
                "independent_boot_receipt": self.receipt,
                "hashes": {
                    str(source / name): digest(source / name)
                    for names in self.names.values()
                    for name in names
                },
            },
        )
        result = boot_reuse.reuse(self.bundle, clock_multiplier=3)
        self.assertEqual(result["clock_multiplier"], 3)
        self.assertEqual(result["reused"], ["dut", "reference"])
        self.assertTrue(boot_cache.valid())
        for name in self.names["dut"]:
            self.assertEqual((self.bbc / name).read_bytes(), (source / name).read_bytes())
        # Reuse publishes only the selected DUT profile, retaining its previous
        # 32 MHz state bytes even when those older bytes are no longer current.
        for name in ["boot.state", "boot.state.fm", "boot.state.ssd", "boot.state.rgb"]:
            self.assertEqual((self.bbc / name).read_bytes(), self.before[name])

    def test_incomplete_run_changed_manifest_and_missing_provenance_reject(self):
        paths = [
            self.bundle / "execution-result.json",
            self.bundle / "execution-bundle.json",
            self.workspace / "build/bbc/latest/report.json",
        ]
        replacements = [
            {"exit_code": None},
            {"independent_file_copies": False},
            {"inputs_unchanged": True, "boot_provenance_complete": False},
        ]
        for path, replacement in zip(paths, replacements):
            original = path.read_bytes()
            write_json(path, replacement)
            with self.assertRaises(ValueError):
                boot_reuse.reuse(self.bundle)
            self.assert_unchanged()
            path.write_bytes(original)

    def test_cross_backend_state_labels_or_changed_post_replay_receipt_reject(self):
        path = self.workspace / "build/bbc/boot-cache.json"
        original = path.read_bytes()
        changed = json.loads(original)
        changed["backends"]["dut"]["states"] = changed["backends"]["reference"]["states"]
        write_json(path, changed)
        with self.assertRaises(ValueError):
            boot_reuse.reuse(self.bundle)
        self.assert_unchanged()

    def test_linked_source_or_destination_reject(self):
        for directory in [self.workspace / "build/bbc", self.bbc]:
            path = directory / "boot.state"
            original = path.read_bytes()
            retained = directory / "linked-state"
            retained.write_bytes(original)
            path.unlink()
            path.symlink_to(retained.name)
            with self.assertRaises(ValueError):
                boot_reuse.reuse(self.bundle)
            path.unlink()
            path.write_bytes(original)
            self.assert_unchanged()

    def test_source_mutation_during_copy_rejects_before_publication(self):
        copy = shutil.copy2
        source = self.workspace / "build/bbc/boot.state"

        def mutate(*args, **kwargs):
            result = copy(*args, **kwargs)
            source.write_bytes(source.read_bytes() + b"changed")
            return result

        with (
            patch.object(boot_reuse.shutil, "copy2", side_effect=mutate),
            self.assertRaises(ValueError),
        ):
            boot_reuse.reuse(self.bundle)
        self.assert_unchanged()

    def test_new_destination_file_during_staging_is_not_overwritten(self):
        target = self.bbc / "boot.state.rgb"
        target.unlink()
        copy = shutil.copy2

        def mutate(*args, **kwargs):
            result = copy(*args, **kwargs)
            target.write_bytes(b"concurrent writer")
            return result

        with (
            patch.object(boot_reuse.shutil, "copy2", side_effect=mutate),
            self.assertRaises(ValueError),
        ):
            boot_reuse.reuse(self.bundle)
        self.assertEqual(target.read_bytes(), b"concurrent writer")

    def test_failed_seal_restores_previous_states_and_receipt(self):
        receipt = self.bbc / "boot-cache.json"
        original = receipt.read_bytes()

        def fail_after_write(path, value):
            write_json(path, value)
            if path == receipt:
                self.assertNotEqual((self.bbc / "boot.state").read_bytes(), self.before["boot.state"])
                raise OSError("simulated sealing failure")

        with (
            patch.object(boot_reuse, "write_json", side_effect=fail_after_write),
            self.assertRaisesRegex(OSError, "simulated sealing failure"),
        ):
            boot_reuse.reuse(self.bundle)

        self.assert_unchanged()
        self.assertEqual(receipt.read_bytes(), original)

    def test_sealed_own_boots_can_be_retained_before_replay_finishes(self):
        (self.bundle / "execution-result.json").unlink()
        (self.workspace / "build/bbc/latest/report.json").unlink()
        with self.assertRaises(FileNotFoundError):
            boot_reuse.reuse(self.bundle)
        self.assert_unchanged()
        result = boot_reuse.reuse(self.bundle, sealed_boots_only=True)
        self.assertTrue(result["sealed_boots_only"])
        self.assertTrue(boot_cache.valid())

    def test_sealed_boot_mode_requires_the_original_captured_receipt(self):
        path = self.workspace / "build/bbc/boot-cache.json"
        changed = json.loads(path.read_text())
        changed["backends"]["dut"]["independent_execution"] = ""
        write_json(path, changed)
        with self.assertRaises(ValueError):
            boot_reuse.reuse(self.bundle, sealed_boots_only=True)
        self.assert_unchanged()


if __name__ == "__main__":
    unittest.main()
