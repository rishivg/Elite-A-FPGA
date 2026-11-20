"""Native FIFO capture retains every byte and rejects incomplete producers."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.bbc import capture, storage
from tools.bbc.audio_stream import AudioStream
from tools.bbc.capture import run
from tools.bbc.host_budget import capture_slot
from tools.validation.common import digest


class AudioStreamTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        permit = patch(
            "tools.bbc.capture.capture_slot", lambda: capture_slot(self.root / "permits", 1)
        )
        permit.start()
        self.addCleanup(permit.stop)

    def producer(self, mode="pass"):
        binary = self.root / "VBbcMachine"
        binary.write_text(
            f"#!{sys.executable}\n"
            "import sys,time\nfrom pathlib import Path\n"
            "data=bytes(range(256))*9000+b'final boundary'\n"
            "with (Path(sys.argv[-1])/'audio.u8').open('wb') as stream:\n"
            " for offset in range(0,len(data),997): stream.write(data[offset:offset+997])\n"
            + ("time.sleep(10)\n" if mode == "timeout" else "")
            + ("raise SystemExit(2)\n" if mode == "fail" else "")
        )
        binary.chmod(0o700)
        return binary

    def test_stream_and_post_exit_pack_have_identical_complete_bytes(self):
        streamed, raw = self.root / "streamed", self.root / "raw"
        raw.mkdir()
        data = bytes(range(256)) * 9000 + b"final boundary"
        (raw / "audio.u8").write_bytes(data)
        storage.pack(raw)
        run([str(self.producer()), str(streamed)], self.root, self.root / "log", pack=streamed)
        self.assertEqual(storage.read_bytes(streamed, "audio.u8"), data)
        self.assertEqual(digest(raw / "audio.u8.gz"), digest(streamed / "audio.u8.gz"))
        self.assertEqual(
            (raw / storage.MANIFEST).read_bytes(), (streamed / storage.MANIFEST).read_bytes()
        )
        self.assertFalse((streamed / "audio.u8").exists())

    def test_failed_spawn_failed_exit_and_timeout_cannot_declare_capture(self):
        for mode in ("spawn", "fail", "timeout"):
            with self.subTest(mode=mode):
                directory = self.root / mode
                binary = (
                    self.root / "missing/VBbcMachine" if mode == "spawn" else self.producer(mode)
                )
                expected = {
                    "spawn": FileNotFoundError,
                    "fail": subprocess.CalledProcessError,
                    "timeout": subprocess.TimeoutExpired,
                }[mode]
                with self.assertRaises(expected):
                    run(
                        [str(binary), str(directory)],
                        self.root,
                        self.root / (mode + ".log"),
                        timeout=0.2 if mode == "timeout" else 10,
                        pack=directory,
                    )
                self.assertFalse((directory / storage.MANIFEST).exists())
                self.assertFalse((directory / "audio.u8").exists())
                self.assertFalse(
                    json.loads((directory / "incomplete-audio-stream.json").read_text())[
                        "qualified"
                    ]
                )
                with self.assertRaisesRegex(ValueError, "Undeclared"):
                    storage.read_bytes(directory, "audio.u8")

    def test_wrong_destination_rejected_before_process_start(self):
        with self.assertRaisesRegex(ValueError, "actual output"):
            run(
                [str(self.producer()), str(self.root / "other")],
                self.root,
                self.root / "log",
                pack=self.root / "capture",
            )
        self.assertFalse((self.root / "capture").exists())

    def test_changed_stream_bytes_cannot_be_sealed(self):
        directory = self.root / "capture"
        audio = AudioStream(directory)
        subprocess.run(
            [
                sys.executable,
                "-c",
                "import sys;open(sys.argv[1],'wb').write(b'original')",
                str(audio.fifo),
            ],
            check=True,
        )
        info = audio.finish(True)
        (directory / "audio.u8.gz").write_bytes((directory / "audio.u8.gz").read_bytes()[:-4])
        with self.assertRaises((ValueError, EOFError)):
            storage.pack(directory, pre_encoded={"audio.u8": info})
        self.assertFalse((directory / storage.MANIFEST).exists())

    def reference_producer(self):
        binary = self.root / "node"
        binary.write_text(
            f"#!{sys.executable}\n"
            "import sys,json\nfrom pathlib import Path\n"
            "directory=Path(json.loads(Path(sys.argv[2]).read_text())['output'])\n"
            "data=bytes(range(256))*9000+b'final boundary'\n"
            "with (directory/'audio-digital.u8').open('wb') as levels, (directory/'audio-pins.u8').open('wb') as pins:\n"
            " for offset in range(0,len(data),997):\n"
            "  levels.write(data[offset:offset+997]);pins.write(data[offset:offset+997][::-1])\n"
        )
        binary.chmod(0o700)
        return binary

    def reference_command(self, directory):
        configuration = self.root / "reference.json"
        configuration.write_text(
            json.dumps({"output": str(directory), "scenario": {"name": "fixture"}})
        )
        return [
            str(self.reference_producer()),
            str(Path(capture.__file__).with_name("reference.mjs")),
            str(configuration),
        ]

    def test_reference_levels_and_pins_stream_independently_without_losing_tail(self):
        directory = self.root / "reference"
        run(self.reference_command(directory), self.root, self.root / "log", pack=directory)
        data = bytes(range(256)) * 9000 + b"final boundary"
        pins = b"".join(data[offset : offset + 997][::-1] for offset in range(0, len(data), 997))
        self.assertEqual(storage.read_bytes(directory, "audio-digital.u8"), data)
        self.assertEqual(storage.read_bytes(directory, "audio-pins.u8"), pins)
        self.assertEqual(
            set(json.loads((directory / storage.MANIFEST).read_text())["files"]),
            {"audio-digital.u8", "audio-pins.u8"},
        )

    def test_second_pipe_failure_closes_first_without_starting_backend(self):
        directory = self.root / "reference"
        directory.mkdir()
        (directory / "audio-pins.u8").write_bytes(b"prior observation")
        command = self.reference_command(directory)
        with patch.object(capture.subprocess, "run") as backend:
            with self.assertRaisesRegex(ValueError, "already exists"):
                run(command, self.root, self.root / "log", pack=directory)
            backend.assert_not_called()
        self.assertFalse((directory / "audio-digital.u8").exists())
        self.assertFalse((directory / storage.MANIFEST).exists())
        self.assertEqual((directory / "audio-pins.u8").read_bytes(), b"prior observation")


if __name__ == "__main__":
    unittest.main()
