"""Execution copies must survive edits and reject a changing source tree."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.bbc import isolated


class ExecutionCopyTests(unittest.TestCase):
    def test_freeze_uses_the_profile_only_when_the_qualification_is_faster(self):
        for faster in (False, True):
            with (
                self.subTest(faster=faster),
                patch("sys.argv", ["isolated", "--clock-multiplier", "3", "--prefer-profile96"]),
                patch("tools.bbc.profile96.faster", return_value=faster),
                patch.object(
                    isolated, "execution_files", side_effect=ValueError("inventory")
                ) as files,
                self.assertRaisesRegex(ValueError, "inventory"),
            ):
                isolated.main()
            files.assert_called_once_with(3, faster)

    def test_failed_copy_retains_originals_and_failure_reason_without_execution(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "runtime.py"
            source.write_bytes(b"original")
            unrelated = root / "existing-proof"
            unrelated.mkdir()
            (unrelated / "capture").write_bytes(b"retained proof")

            def failed_copy(files, destination):
                destination.mkdir()
                (destination / "partial.py").write_bytes(b"partial copy")
                raise OSError("copy ran out of space")

            with (
                patch.object(isolated, "execution_files", return_value=[source]),
                patch.object(isolated.build, "BBC", root),
                patch.object(isolated, "copy_bundle", side_effect=failed_copy),
                patch.object(isolated.shutil, "disk_usage") as usage,
                patch.object(isolated.subprocess, "run") as execute,
            ):
                usage.return_value.free = 1024 * 1024 * 1024
                with self.assertRaisesRegex(OSError, "out of space"):
                    isolated.main()
                execute.assert_not_called()
            copies = list(root.glob("execution-*"))
            self.assertEqual(len(copies), 1)
            self.assertFalse((copies[0] / "workspace").exists())
            reason = json.loads((copies[0] / "INCOMPLETE.json").read_text())
            self.assertFalse(reason["passed"])
            self.assertIn("out of space", reason["error"])
            self.assertEqual(source.read_bytes(), b"original")
            self.assertEqual((unrelated / "capture").read_bytes(), b"retained proof")

    def test_insufficient_space_fails_before_creating_a_copy(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "runtime.py"
            source.write_bytes(b"original")
            with (
                patch.object(isolated, "execution_files", return_value=[source]),
                patch.object(isolated.shutil, "disk_usage") as usage,
                patch.object(isolated.tempfile, "mkdtemp") as create,
            ):
                usage.return_value.free = 0
                with self.assertRaisesRegex(ValueError, "capture headroom"):
                    isolated.main()
                create.assert_not_called()

    def test_files_are_independent_after_the_original_is_edited(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            source.mkdir()
            module = source / "runtime.py"
            module.write_bytes(b"original execution input")
            target = root / "execution"
            with patch.object(isolated, "ROOT", source):
                inputs = isolated.copy_bundle([module], target)
            module.write_bytes(b"later working tree edit")
            self.assertEqual((target / "runtime.py").read_bytes(), b"original execution input")
            self.assertEqual(len(inputs), 1)

    def test_source_mutation_while_copying_fails(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            source.mkdir()
            module = source / "runtime.py"
            module.write_bytes(b"original")
            copy = isolated.shutil.copy2

            def changing_copy(path, destination):
                copy(path, destination)
                path.write_bytes(b"changed during copying")

            with (
                patch.object(isolated, "ROOT", source),
                patch.object(isolated.shutil, "copy2", side_effect=changing_copy),
                self.assertRaisesRegex(ValueError, "changed while creating"),
            ):
                isolated.copy_bundle([module], root / "execution")

    def test_a_hard_link_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            source.mkdir()
            module = source / "runtime.py"
            module.write_bytes(b"original")

            def linked_copy(path, destination):
                destination.hardlink_to(path)

            with (
                patch.object(isolated, "ROOT", source),
                patch.object(isolated.shutil, "copy2", side_effect=linked_copy),
                self.assertRaisesRegex(ValueError, "shares an inode"),
            ):
                isolated.copy_bundle([module], root / "execution")

    def test_relative_dependency_links_keep_private_targets_and_inventory(self):
        from tools.validation.setup import dependency_inventory

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            module = source / "node_modules/package/index.js"
            module.parent.mkdir(parents=True)
            module.write_bytes(b"original dependency")
            link = source / "node_modules/.bin/tool"
            link.parent.mkdir()
            link.symlink_to("../package/index.js")
            target = root / "execution"
            inventory = dependency_inventory(source)
            with patch.object(isolated, "ROOT", source):
                isolated.copy_bundle([link, module], target)
            self.assertEqual(dependency_inventory(target), inventory)
            self.assertEqual(
                (target / "node_modules/.bin/tool").resolve(),
                target / "node_modules/package/index.js",
            )
            module.write_bytes(b"later source edit")
            self.assertEqual(
                (target / "node_modules/.bin/tool").read_bytes(), b"original dependency"
            )

    def test_absolute_link_is_rejected_before_it_can_point_into_the_source(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            source.mkdir()
            module = source / "runtime.py"
            module.write_bytes(b"original")
            link = source / "linked.py"
            link.symlink_to(module)
            with (
                patch.object(isolated, "ROOT", source),
                self.assertRaisesRegex(ValueError, "absolute source link"),
            ):
                isolated.copy_bundle([link, module], root / "execution")

    def test_changed_link_target_is_rejected_even_when_target_bytes_match(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            source.mkdir()
            modules = [source / name for name in ["one.py", "two.py"]]
            for module in modules:
                module.write_bytes(b"same bytes")
            link = source / "linked.py"
            link.symlink_to("one.py")
            copy = isolated.shutil.copy2

            def changed_link(path, destination):
                copy(path, destination)
                if path == modules[0]:
                    link.unlink()
                    link.symlink_to("two.py")

            with (
                patch.object(isolated, "ROOT", source),
                patch.object(isolated.shutil, "copy2", side_effect=changed_link),
                self.assertRaisesRegex(ValueError, "links changed"),
            ):
                isolated.copy_bundle([link, *modules], root / "execution")
