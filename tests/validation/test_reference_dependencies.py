"""Dependency cache byte guards and concurrent first-time setup."""

import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from unittest.mock import patch

from tools.validation import setup


class ReferenceDependencyTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.emulator = self.root / "emulator"
        self.emulator.mkdir()
        (self.emulator / "package.json").write_text('{"dependencies":{"pako":"1.0.0"}}')
        (self.emulator / "package-lock.json").write_text('{"lockfileVersion":3}')
        self.module = self.emulator / "node_modules/pako/index.js"
        cache = patch.object(setup, "CACHE", self.root / "cache")
        cache.start()
        self.addCleanup(cache.stop)

    def install(self, *args, **kwargs):
        self.module.parent.mkdir(parents=True, exist_ok=True)
        self.module.write_bytes(b"original dependency")

    def seed(self):
        with patch.object(setup.subprocess, "run", side_effect=self.install) as installer:
            receipt = setup.prepare_dependencies(self.emulator, "v24.19.0")
            self.assertEqual(installer.call_count, 1)
            self.assertIn("--ignore-scripts", installer.call_args.args[0])
        return receipt

    def test_verified_hit_preserves_files_and_never_reinstalls(self):
        expected = self.seed()
        inode = self.module.stat().st_ino
        with patch.object(setup.subprocess, "run") as installer:
            self.assertEqual(setup.prepare_dependencies(self.emulator, "v24.19.0"), expected)
            installer.assert_not_called()
        self.assertEqual(self.module.stat().st_ino, inode)

    def test_same_size_timestamp_mutation_is_rejected_without_repair(self):
        self.seed()
        previous = self.module.stat()
        self.module.write_bytes(b"modified dependency")
        os.utime(self.module, ns=(previous.st_atime_ns, previous.st_mtime_ns))
        with patch.object(setup.subprocess, "run") as installer:
            with self.assertRaisesRegex(ValueError, "dependency bytes changed"):
                setup.prepare_dependencies(self.emulator, "v24.19.0")
            installer.assert_not_called()

    def test_missing_and_added_files_invalidate_the_inventory(self):
        self.seed()
        original = self.module.read_bytes()
        self.module.unlink()
        with self.assertRaisesRegex(ValueError, "Missing installed"):
            setup.prepare_dependencies(self.emulator, "v24.19.0")
        self.module.write_bytes(original)
        (self.module.parent / "unexpected.js").write_text("added")
        with self.assertRaisesRegex(ValueError, "dependency bytes changed"):
            setup.prepare_dependencies(self.emulator, "v24.19.0")

    def test_changed_setup_requires_explicit_repair(self):
        self.seed()
        with patch.object(setup.subprocess, "run") as installer:
            with self.assertRaisesRegex(ValueError, "setup changed"):
                setup.prepare_dependencies(self.emulator, "v25.0.0")
            installer.assert_not_called()
        with patch.object(setup.subprocess, "run", side_effect=self.install) as installer:
            repaired = setup.prepare_dependencies(self.emulator, "v25.0.0", reinstall=True)
            self.assertEqual(installer.call_count, 1)
        self.assertEqual(repaired["inputs"]["node"], "v25.0.0")

    def test_concurrent_initialization_installs_once_before_readers_return(self):
        entered, release, waiting = Event(), Event(), Event()

        def installer(*args, **kwargs):
            entered.set()
            if not release.wait(timeout=5):
                raise RuntimeError("Installer was not released")
            self.install()

        def second():
            waiting.set()
            return setup.prepare_dependencies(self.emulator, "v24.19.0")

        with patch.object(setup.subprocess, "run", side_effect=installer) as install:
            with ThreadPoolExecutor(max_workers=2) as executor:
                first = executor.submit(setup.prepare_dependencies, self.emulator, "v24.19.0")
                try:
                    self.assertTrue(entered.wait(timeout=5))
                    following = executor.submit(second)
                    self.assertTrue(waiting.wait(timeout=5))
                    self.assertFalse(following.done())
                finally:
                    release.set()
                self.assertEqual(first.result(timeout=5), following.result(timeout=5))
            self.assertEqual(install.call_count, 1)

    def test_dependency_link_cannot_escape_the_installed_tree(self):
        self.seed()
        outside = self.root / "outside.js"
        outside.write_text("external")
        (self.module.parent / "linked.js").symlink_to(outside)
        with self.assertRaisesRegex(ValueError, "link escapes"):
            setup.prepare_dependencies(self.emulator, "v24.19.0")
