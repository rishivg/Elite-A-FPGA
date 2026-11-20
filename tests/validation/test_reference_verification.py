"""Cached archive decoding must still reject changed archive or live source bytes."""

import hashlib
import io
import os
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.validation import setup


class ReferenceVerificationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / "downloads").mkdir()
        self.source = self.root / "source"
        self.source.mkdir()
        files = {
            "src/device.js": b"original device",
            "5-compiled-game-discs/elite-a-released.ssd": bytes(512),
        }
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
            for name, data in files.items():
                info = tarfile.TarInfo("snapshot/" + name)
                info.size = len(data)
                archive.addfile(info, io.BytesIO(data))
                path = self.source / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
        self.archive = self.root / "downloads/elite_a.tar.gz"
        self.archive.write_bytes(buffer.getvalue())
        lock = {
            "assets": {},
            "sources": {
                "elite_a": {"archive_sha256": hashlib.sha256(buffer.getvalue()).hexdigest()}
            },
            "required_disc_files": 0,
        }
        for name, value in [("CACHE", self.root), ("LOCK", lock)]:
            replacement = patch.object(setup, name, value)
            replacement.start()
            self.addCleanup(replacement.stop)
        for name, value in [("source_root", self.source), ("reference_symbols", {})]:
            replacement = patch.object(setup, name, return_value=value)
            replacement.start()
            self.addCleanup(replacement.stop)
        setup._archive_manifest.cache_clear()
        self.addCleanup(setup._archive_manifest.cache_clear)

    def test_reuses_decoding_but_rejects_same_timestamp_source_mutation(self):
        with patch.object(setup.tarfile, "open", wraps=tarfile.open) as opened:
            setup.verify()
            setup.verify()
            self.assertEqual(opened.call_count, 1)
            path = self.source / "src/device.js"
            stat = path.stat()
            path.write_bytes(b"changed! device")
            os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
            with self.assertRaisesRegex(ValueError, "Reference source modified"):
                setup.verify()
            self.assertEqual(opened.call_count, 1)

    def test_cached_manifest_does_not_accept_changed_archive(self):
        setup.verify()
        stat = self.archive.stat()
        data = bytearray(self.archive.read_bytes())
        data[-1] ^= 1
        self.archive.write_bytes(data)
        os.utime(self.archive, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        with self.assertRaisesRegex(ValueError, "Archive checksum mismatch"):
            setup.verify()
