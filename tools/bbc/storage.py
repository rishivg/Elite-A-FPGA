"""Pack completed captures and verify the decoded bytes."""

from __future__ import annotations

import gzip
import hashlib
import json
import zlib
from contextlib import contextmanager
from pathlib import Path

from tools.validation.common import digest, write_json

MANIFEST = "capture-storage.json"
SUFFIXES = {".u8", ".ram", ".rgb", ".rgba", ".ssd", ".fm"}
CHUNK = 1 << 20


def entry(directory, name):
    """Explicit declarations prevent fallback from damaged encoded observations."""
    path = directory / MANIFEST
    if not path.exists():
        return None
    saved = json.loads(path.read_text())
    if (
        not isinstance(saved, dict)
        or saved.get("schema") != 1
        or not isinstance(saved.get("files"), dict)
    ):
        raise ValueError("Invalid capture storage declaration")
    for filename, info in saved["files"].items():
        if (
            not isinstance(filename, str)
            or Path(filename).name != filename
            or Path(filename).suffix not in SUFFIXES
            or not isinstance(info, dict)
            or info.get("encoding") != "gzip"
            or info.get("file") != filename + ".gz"
            or type(info.get("bytes")) is not int
            or info["bytes"] < 0
            or not isinstance(info.get("sha256"), str)
            or len(info["sha256"]) != 64
            or any(c not in "0123456789abcdef" for c in info["sha256"])
        ):
            raise ValueError("Invalid capture storage entry")
        if (directory / filename).exists() or not (directory / info["file"]).is_file():
            raise ValueError("Missing or ambiguous encoded capture")
    return saved["files"].get(name)


def length(directory, name):
    info = entry(directory, name)
    return info["bytes"] if info else (directory / name).stat().st_size


class CheckedReader:
    """Hash every decompressed byte, including a consumer's unread trailing data."""

    def __init__(self, stream, info):
        self.stream = stream
        self.info = info
        self.bytes = 0
        self.sha = hashlib.sha256()

    def read(self, size=-1):
        data = self.stream.read(size)
        self.bytes += len(data)
        self.sha.update(data)
        if self.bytes > self.info["bytes"]:
            raise ValueError("Excess decoded capture bytes")
        return data

    def finish(self):
        while self.read(CHUNK):
            pass
        if self.bytes != self.info["bytes"] or self.sha.hexdigest() != self.info["sha256"]:
            raise ValueError("Incomplete or changed decoded capture bytes")


@contextmanager
def open_capture(directory, name):
    info = entry(directory, name)
    if info is None:
        # Undeclared compressed data never substitutes for missing original bytes.
        if (directory / (name + ".gz")).exists():
            raise ValueError("Undeclared compressed capture")
        with (directory / name).open("rb") as stream:
            yield stream
        return
    try:
        with gzip.open(directory / info["file"], "rb") as stream:
            checked = CheckedReader(stream, info)
            yield checked
            checked.finish()
    except (EOFError, gzip.BadGzipFile, zlib.error) as error:
        raise ValueError("Invalid compressed capture") from error


def read_bytes(directory, name):
    with open_capture(directory, name) as stream:
        return stream.read()


def pack(directory, pre_encoded=None):
    """Verify encoded bytes before removing any original; called after child exit."""
    if (directory / MANIFEST).exists():
        raise ValueError("Capture storage already declared")
    if any(directory.glob("*.state")):
        raise ValueError("Keep own-machine checkpoint states and sidecars raw")
    sources = sorted(p for p in directory.iterdir() if p.is_file() and p.suffix in SUFFIXES)
    encoded = {} if pre_encoded is None else dict(pre_encoded)
    for name, info in encoded.items():
        if (
            not isinstance(name, str)
            or not isinstance(info, dict)
            or Path(name).name != name
            or Path(name).suffix not in SUFFIXES
            or info.get("encoding") != "gzip"
            or info.get("file") != name + ".gz"
            or type(info.get("bytes")) is not int
            or info["bytes"] < 0
            or not isinstance(info.get("sha256"), str)
            or len(info["sha256"]) != 64
            or any(c not in "0123456789abcdef" for c in info["sha256"])
            or (directory / name).exists()
        ):
            raise ValueError("Invalid streamed capture declaration")
        with gzip.open(directory / info["file"], "rb") as stream:
            CheckedReader(stream, info).finish()
    for source in sources:
        target = source.with_name(source.name + ".gz")
        if target.exists():
            raise ValueError("Capture encoding already exists")
        size = source.stat().st_size
        original_sha = hashlib.sha256()
        with (
            source.open("rb") as original,
            target.open("wb") as output,
            gzip.GzipFile(
                filename="", mode="wb", fileobj=output, compresslevel=1, mtime=0
            ) as compressed,
        ):
            while chunk := original.read(CHUNK):
                original_sha.update(chunk)
                compressed.write(chunk)
        expected = original_sha.hexdigest()
        count = 0
        sha = hashlib.sha256()
        with gzip.open(target, "rb") as verify:
            while chunk := verify.read(CHUNK):
                count += len(chunk)
                sha.update(chunk)
        if count != size or sha.hexdigest() != expected or digest(source) != expected:
            raise ValueError("Capture bytes changed during lossless encoding")
        encoded[source.name] = {
            "encoding": "gzip",
            "file": target.name,
            "bytes": size,
            "sha256": expected,
        }
    # Publish the storage manifest only after the files are packed.
    for source in sources:
        source.unlink()
    write_json(directory / MANIFEST, {"schema": 1, "files": encoded})
