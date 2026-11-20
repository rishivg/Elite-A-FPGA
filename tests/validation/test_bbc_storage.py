"""Lossless capture storage preserves comparisons and rejects damaged evidence."""

import gzip
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.bbc import storage
from tools.bbc.capture import run
from tools.bbc.compare import captured_audio
from tools.bbc.host_budget import capture_slot
from tools.validation.common import digest


class CaptureStorageTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        # Tiny fixture processes exercise the real lease protocol in their
        # own pool, so long game replays cannot block the unit-test suite.
        permit = patch(
            "tools.bbc.capture.capture_slot",
            lambda: capture_slot(self.root / "permits", 1),
        )
        permit.start()
        self.addCleanup(permit.stop)

    def fixture(self, name="capture"):
        directory = self.root / name
        directory.mkdir()
        data = bytes(range(256)) * 4096 + b"boundary!"
        (directory / "audio.u8").write_bytes(data)
        (directory / "checkpoint.ram").write_bytes(data[:65536])
        (directory / "checkpoint.rgb").write_bytes(data[:380928])
        (directory / "checkpoint.ssd").write_bytes(data[:204800])
        (directory / "checkpoint.fm").write_bytes(data[:500000])
        return directory, data

    def test_all_bytes_and_deterministic_artifacts_survive_encoding(self):
        first, data = self.fixture("first")
        second, _ = self.fixture("second")
        storage.pack(first)
        storage.pack(second)
        self.assertFalse((first / "audio.u8").exists())
        self.assertEqual(storage.length(first, "audio.u8"), len(data))
        for filename, expected in [
            ("audio.u8", data),
            ("checkpoint.ram", data[:65536]),
            ("checkpoint.rgb", data[:380928]),
            ("checkpoint.ssd", data[:204800]),
            ("checkpoint.fm", data[:500000]),
        ]:
            self.assertEqual(storage.read_bytes(first, filename), expected)
        self.assertEqual(
            {f.name: digest(f) for f in first.iterdir()},
            {f.name: digest(f) for f in second.iterdir()},
        )

    def test_crc_truncation_extra_members_and_wrong_digest_fail(self):
        directory, _ = self.fixture()
        storage.pack(directory)
        path = directory / "audio.u8.gz"
        original = path.read_bytes()
        crc = bytearray(original)
        crc[-8] ^= 1
        for broken in [original[:-8], bytes(crc), original + gzip.compress(b"x", mtime=0)]:
            with self.subTest(bytes=len(broken)):
                path.write_bytes(broken)
                with self.assertRaises(ValueError):
                    storage.read_bytes(directory, "audio.u8")
        path.write_bytes(original)
        declaration = directory / storage.MANIFEST
        metadata = json.loads(declaration.read_text())
        metadata["files"]["audio.u8"]["sha256"] = "0" * 64
        declaration.write_text(json.dumps(metadata))
        with self.assertRaisesRegex(ValueError, "changed decoded"):
            storage.read_bytes(directory, "audio.u8")

    def test_unread_tail_is_also_verified(self):
        directory, _ = self.fixture()
        storage.pack(directory)
        path = directory / "audio.u8.gz"
        data = bytearray(gzip.decompress(path.read_bytes()))
        data[-1] ^= 1
        path.write_bytes(gzip.compress(data, mtime=0))
        with (
            self.assertRaisesRegex(ValueError, "changed decoded"),
            storage.open_capture(directory, "audio.u8") as stream,
        ):
            stream.read(1)

    def test_ambiguous_undeclared_missing_and_redirected_entries_fail(self):
        directory, data = self.fixture()
        storage.pack(directory)
        (directory / "audio.u8").write_bytes(data)
        with self.assertRaisesRegex(ValueError, "ambiguous"):
            storage.read_bytes(directory, "audio.u8")
        (directory / "audio.u8").unlink()
        declaration = directory / storage.MANIFEST
        original = declaration.read_text()
        metadata = json.loads(original)
        metadata["files"]["audio.u8"]["file"] = "../different.gz"
        declaration.write_text(json.dumps(metadata))
        with self.assertRaisesRegex(ValueError, "Invalid capture storage entry"):
            storage.read_bytes(directory, "audio.u8")
        declaration.write_text(original)
        (directory / "audio.u8.gz").unlink()
        with self.assertRaisesRegex(ValueError, "Missing"):
            storage.read_bytes(directory, "audio.u8")
        declaration.unlink()
        (directory / "audio.u8").write_bytes(data)
        (directory / "audio.u8.gz").write_bytes(gzip.compress(data, mtime=0))
        with self.assertRaisesRegex(ValueError, "Undeclared"):
            storage.read_bytes(directory, "audio.u8")

    def test_packed_audio_keeps_absolute_mismatches_and_length_failures(self):
        reference, dut = self.root / "reference", self.root / "dut"
        reference.mkdir()
        dut.mkdir()
        count = (1 << 20) + 7
        (reference / "audio.f32").write_bytes(bytes(count * 4))
        (reference / "audio-digital.u8").write_bytes(bytes(count))
        values = bytearray(count)
        for index in [(1 << 20) - 1, 1 << 20, count - 1]:
            values[index] = 63
        (dut / "audio.u8").write_bytes(values)
        storage.pack(reference)
        storage.pack(dut)
        scenario, summary = {"duration_us": count * 2}, {"duration_cycles": count * 4}
        result = captured_audio(self.root, scenario, summary)["digital_waveform"]
        self.assertFalse(result["passed"])
        self.assertEqual(result["different_samples"], 3)
        self.assertEqual(result["first_difference_sample"], (1 << 20) - 1)
        with self.assertRaisesRegex(ValueError, "invalid audio samples"):
            captured_audio(self.root, {"duration_us": (count + 1) * 2}, summary)

    def test_saved_machine_states_and_sidecars_cannot_be_packed(self):
        directory, data = self.fixture()
        (directory / "own.state").write_bytes(b"own model state")
        (directory / "own.state.ssd").write_bytes(data[:204800])
        with self.assertRaisesRegex(ValueError, "checkpoint states"):
            storage.pack(directory)
        self.assertFalse((directory / storage.MANIFEST).exists())
        self.assertEqual((directory / "audio.u8").read_bytes(), data)

    def test_backend_packs_only_after_successful_exit(self):
        script = (
            "import sys; from pathlib import Path; "
            "(Path(sys.argv[1])/'audio.u8').write_bytes(bytes([1,255])); "
            "sys.exit(int(sys.argv[2]))"
        )
        for code in [0, 2]:
            directory = self.root / str(code)
            directory.mkdir()
            command = [sys.executable, "-c", script, str(directory), str(code)]
            if code:
                with self.assertRaises(subprocess.CalledProcessError):
                    run(command, self.root, directory / "log", pack=directory)
                self.assertFalse((directory / storage.MANIFEST).exists())
                self.assertEqual((directory / "audio.u8").read_bytes(), bytes([1, 255]))
            else:
                run(command, self.root, directory / "log", pack=directory)
                self.assertFalse((directory / "audio.u8").exists())
                self.assertEqual(storage.read_bytes(directory, "audio.u8"), bytes([1, 255]))

    def test_incomplete_prefix_exit_requires_explicit_acceptance(self):
        command = [sys.executable, "-c", "raise SystemExit(1)"]
        with self.assertRaises(subprocess.CalledProcessError):
            run(command, self.root, self.root / "rejected.log")
        run(command, self.root, self.root / "prefix.log", exit_codes=(1,))
        with self.assertRaises(subprocess.CalledProcessError):
            run(
                [sys.executable, "-c", "raise SystemExit(2)"],
                self.root,
                self.root / "error.log",
                exit_codes=(1,),
            )


if __name__ == "__main__":
    unittest.main()
