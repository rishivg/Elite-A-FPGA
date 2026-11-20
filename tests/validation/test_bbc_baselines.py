"""Historical proof sources stay usable and reject changed or unknown inputs."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.bbc import baselines


class BaselineTests(unittest.TestCase):
    def test_all_pinned_sources_load_without_git(self):
        manifest = json.loads((baselines.DIRECTORY / "manifest.json").read_text())
        self.assertEqual(sum(len(paths) for paths in manifest.values()), 10)
        with patch("subprocess.check_output", side_effect=AssertionError("Git not available")):
            for commit, paths in manifest.items():
                dependencies = baselines.dependencies(commit)
                self.assertIn(Path(baselines.__file__), dependencies)
                self.assertIn(baselines.DIRECTORY / "manifest.json", dependencies)
                for path in paths:
                    with self.subTest(commit=commit, path=path):
                        self.assertTrue(baselines.historical(commit, path).startswith("package "))
                        self.assertIn(baselines.DIRECTORY / commit / path, dependencies)

    def test_unknown_commit_or_path_is_rejected(self):
        commit = next(iter(json.loads((baselines.DIRECTORY / "manifest.json").read_text())))
        for candidate, path in [("unknown", "rtl/bbc/BbcVideo.scala"), (commit, "../LICENSE")]:
            with (
                self.subTest(commit=candidate, path=path),
                self.assertRaisesRegex(ValueError, "Unknown proof baseline"),
            ):
                baselines.historical(candidate, path)

    def test_changed_source_is_rejected(self):
        manifest = json.loads((baselines.DIRECTORY / "manifest.json").read_text())
        commit = next(iter(manifest))
        path = next(iter(manifest[commit]))
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory / "manifest.json").write_text(json.dumps(manifest))
            source = directory / commit / path
            source.parent.mkdir(parents=True)
            source.write_text("changed baseline\n")
            with (
                patch.object(baselines, "DIRECTORY", directory),
                self.assertRaisesRegex(ValueError, "Proof baseline checksum mismatch"),
            ):
                baselines.historical(commit, path)


if __name__ == "__main__":
    unittest.main()
