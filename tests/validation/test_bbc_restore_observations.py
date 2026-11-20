"""Archived independent observations must restore without sharing inodes or overwriting data."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.bbc import restore_observations
from tools.validation.common import digest


class RestoreObservationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        archive = self.root / "archive"
        archive.mkdir()
        blob = archive / "data"
        blob.write_bytes(b"independent observation\n")
        self.sha = digest(blob)
        blob.rename(archive / (self.sha + ".blob"))
        self.inventory = archive / "inventory.json"
        self.member = {"sha256": self.sha, "bytes": 24, "mode": 0o600, "blob": self.sha + ".blob"}
        self.members = {f"build/bbc/capture{i}/audio": self.member for i in (1, 2)}
        self.save()

    def save(self):
        self.inventory.write_text(json.dumps({"schema": 1, "members": self.members}))

    def test_copies_are_distinct_and_prefix_can_limit_restoration(self):
        with patch.object(restore_observations, "ROOT", self.root):
            self.assertEqual(restore_observations.restore(self.inventory, "build/bbc/capture1"), 1)
            self.assertFalse((self.root / "build/bbc/capture2/audio").exists())
            self.assertEqual(restore_observations.restore(self.inventory), 2)
        first, second = [self.root / f"build/bbc/capture{i}/audio" for i in (1, 2)]
        self.assertEqual(digest(first), self.sha)
        self.assertNotEqual(first.stat().st_ino, second.stat().st_ino)
        self.assertEqual(first.stat().st_mode & 0o777, 0o600)

    def test_existing_different_observation_aborts_before_copying(self):
        destination = self.root / "build/bbc/capture2/audio"
        destination.parent.mkdir(parents=True)
        destination.write_bytes(b"other observation")
        with (
            patch.object(restore_observations, "ROOT", self.root),
            self.assertRaisesRegex(ValueError, "refusing to overwrite"),
        ):
            restore_observations.restore(self.inventory)
        self.assertFalse((self.root / "build/bbc/capture1/audio").exists())
        self.assertEqual(destination.read_bytes(), b"other observation")

    def test_escape_paths_and_damaged_blobs_are_rejected(self):
        self.members = {"build/bbc/../../../outside": self.member}
        self.save()
        with (
            patch.object(restore_observations, "ROOT", self.root),
            self.assertRaisesRegex(ValueError, "escapes"),
        ):
            restore_observations.restore(self.inventory)
        self.members = {"build/bbc/capture/audio": self.member}
        self.save()
        (self.inventory.parent / (self.sha + ".blob")).write_bytes(b"damaged")
        with (
            patch.object(restore_observations, "ROOT", self.root),
            self.assertRaisesRegex(ValueError, "damaged"),
        ):
            restore_observations.restore(self.inventory)


if __name__ == "__main__":
    unittest.main()
