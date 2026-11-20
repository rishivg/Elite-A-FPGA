"""Native frame acceleration must reject stale sources and modified executables."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.validation import demo_build
from tools.validation.common import digest, write_json


class DemoCacheTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.binary = self.directory / "native/VSoc"
        self.binary.parent.mkdir()
        self.binary.write_bytes(b"compiled simulator")
        self.source = self.directory / "source.scala"
        self.source.write_bytes(b"current RTL")
        self.rtl = self.directory / "Soc.v"
        self.rtl.write_bytes(b"generated RTL")
        self.memory = self.directory / "Soc.v_ram.bin"
        self.memory.write_bytes(b"firmware memory")
        self.addCleanup(patch.stopall)
        patch.object(demo_build, "DIRECTORY", self.directory).start()
        patch.object(demo_build, "BINARY", self.binary).start()
        self.inputs = patch.object(
            demo_build, "inputs", side_effect=lambda: {"source": digest(self.source), "compiler": 1}
        ).start()
        write_json(
            self.directory / "native-build.json",
            {"inputs": demo_build.inputs(), "outputs": demo_build.outputs()},
        )

    def test_unchanged_build_is_current(self):
        self.assertTrue(demo_build.current())

    def test_source_and_compiler_changes_invalidate_build(self):
        self.source.write_bytes(b"changed RTL")
        self.assertFalse(demo_build.current())
        self.source.write_bytes(b"current RTL")
        self.inputs.side_effect = lambda: {"source": digest(self.source), "compiler": 2}
        self.assertFalse(demo_build.current())

    def test_each_output_mutation_or_removal_invalidates_build(self):
        for path in [self.binary, self.rtl, self.memory]:
            original = path.read_bytes()
            path.write_bytes(original + b"changed")
            self.assertFalse(demo_build.current(), path.name)
            path.unlink()
            self.assertFalse(demo_build.current(), path.name)
            path.write_bytes(original)
            self.assertTrue(demo_build.current())

    def test_unexpected_memory_invalidates_build(self):
        (self.directory / "Soc.v_new.bin").write_bytes(b"new memory")
        self.assertFalse(demo_build.current())

    def test_malformed_or_incomplete_receipt_invalidates_build(self):
        receipt = self.directory / "native-build.json"
        for text in ["{broken", "{}", json.dumps({"inputs": demo_build.inputs()})]:
            receipt.write_text(text)
            self.assertFalse(demo_build.current())


if __name__ == "__main__":
    unittest.main()
