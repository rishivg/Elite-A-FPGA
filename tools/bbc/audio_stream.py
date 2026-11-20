"""Encode producer audio bytes without retaining a large raw capture."""

from __future__ import annotations

import gzip
import hashlib
import os
from pathlib import Path
from threading import Thread

from tools.validation.common import write_json

from .storage import CHUNK, MANIFEST, SUFFIXES


class AudioStream:
    def __init__(self, directory, name="audio.u8"):
        if (
            not isinstance(name, str)
            or Path(name).name != name
            or Path(name).suffix not in SUFFIXES
        ):
            raise ValueError("Invalid audio stream filename")
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.fifo = self.directory / name
        self.encoded = self.directory / (name + ".gz")
        if self.fifo.exists() or self.encoded.exists() or (self.directory / MANIFEST).exists():
            raise ValueError("Audio output already exists")
        self.size = 0
        self.sha = hashlib.sha256()
        self.error = None
        os.mkfifo(self.fifo, 0o600)
        owner = self.fifo.stat()
        self.owner = owner.st_dev, owner.st_ino
        self.reader = self.keepalive = None
        try:
            self.reader = os.open(self.fifo, os.O_RDONLY | os.O_NONBLOCK)
            # Keep EOF away until the backend exits, including failed process
            # startup. This writer is never inherited by the subprocess.
            self.keepalive = os.open(self.fifo, os.O_WRONLY | os.O_NONBLOCK)
            os.set_blocking(self.reader, True)
            self.thread = Thread(target=self.encode, daemon=True)
            self.thread.start()
        except BaseException:
            for descriptor in (self.reader, self.keepalive):
                if descriptor is not None:
                    os.close(descriptor)
            self.fifo.unlink()
            raise

    def encode(self):
        try:
            with (
                os.fdopen(self.reader, "rb") as source,
                self.encoded.open("wb") as output,
                gzip.GzipFile(
                    filename="", mode="wb", fileobj=output, compresslevel=1, mtime=0
                ) as compressed,
            ):
                while chunk := source.read(CHUNK):
                    self.size += len(chunk)
                    self.sha.update(chunk)
                    compressed.write(chunk)
        except BaseException as error:
            self.error = error

    def finish(self, successful):
        os.close(self.keepalive)
        self.thread.join(timeout=60)
        if self.thread.is_alive():
            raise ValueError("Audio encoder did not finish after backend exit")
        owner = self.fifo.stat()
        if (owner.st_dev, owner.st_ino) != self.owner:
            raise ValueError("Audio pipe changed during capture")
        self.fifo.unlink()
        info = {
            "encoding": "gzip",
            "file": self.encoded.name,
            "bytes": self.size,
            "sha256": self.sha.hexdigest(),
        }
        if self.error is not None or not successful:
            write_json(
                self.directory
                / (
                    "incomplete-audio-stream.json"
                    if self.fifo.name == "audio.u8"
                    else "incomplete-" + self.fifo.name + "-stream.json"
                ),
                {
                    "qualified": False,
                    "audio": info,
                    "error": str(self.error) if self.error else "Backend failed",
                    "scope": "Incomplete producer stream; no capture-storage declaration or passing comparison.",
                },
            )
        if self.error is not None:
            raise ValueError("Audio stream encoding failed") from self.error
        return info if successful else None
