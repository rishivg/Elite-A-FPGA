"""Profile optimization requires unchanged compiler inputs and exact captures."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.bbc import profile
from tools.validation.common import ROOT, digest, write_json


class ProfileReceiptTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(dir=ROOT / "build")
        self.addCleanup(temp.cleanup)
        directory = Path(temp.name)
        self.addCleanup(patch.stopall)
        for name, value in [
            ("DIRECTORY", directory),
            ("NATIVE", directory / "native"),
            ("DATA", directory / "counters"),
            ("RECEIPT", directory / "receipt.json"),
        ]:
            patch.object(profile, name, value).start()
        profile.NATIVE.mkdir()
        profile.DATA.mkdir()
        self.binary = profile.NATIVE / "VBbcMachine"
        self.binary.write_bytes(b"compiled model")
        self.counter = profile.DATA / "model.gcda"
        self.counter.write_bytes(b"training counter")
        self.inputs = patch.object(
            profile.build, "native_inputs", return_value={"source": "verified"}
        ).start()
        cases = []
        for name in ["equipment", "flight-combat"]:
            for mode in ["baseline", "optimized"]:
                path = directory / name / mode / "capture"
                path.mkdir(parents=True)
                (path / "ram").write_bytes(b"original guest memory")
            hashes = profile.artifacts(directory / name / "baseline" / "capture")
            cases.append(
                {"name": name, "baseline": {"hashes": hashes}, "optimized": {"hashes": hashes}}
            )
        self.receipt = {
            "schema": 2,
            "target": profile.target_info(),
            "inputs": profile.build.native_inputs(),
            "recipe": digest(Path(profile.__file__)),
            "capture_sources": profile.capture_sources(),
            "counters": profile.build.hashes(profile.DATA.glob("*.gcda")),
            "binary": digest(self.binary),
            "verification": cases,
        }
        write_json(profile.RECEIPT, self.receipt)

    def test_verified_profile_can_be_reused(self):
        self.assertTrue(profile.current(self.receipt))

    def test_mutations_or_missing_evidence_prevent_reuse(self):
        for path in [
            self.binary,
            self.counter,
            profile.DIRECTORY / "equipment/optimized/capture/ram",
            profile.RECEIPT,
        ]:
            original = path.read_bytes()
            path.write_bytes(original + b"changed")
            self.assertFalse(profile.current(self.receipt), str(path))
            path.write_bytes(original)
        self.inputs.return_value = {"source": "changed"}
        self.assertFalse(profile.current(self.receipt))
        self.inputs.return_value = {"source": "verified"}
        self.counter.unlink()
        self.assertFalse(profile.current(self.receipt))

    def test_changed_schedule_writer_prevents_reuse(self):
        changed = dict(self.receipt["capture_sources"])
        changed["tools/bbc/inputs.py"] = "changed"
        with patch.object(profile, "capture_sources", return_value=changed):
            self.assertFalse(profile.current(self.receipt))

    def test_changed_host_instruction_set_prevents_reuse(self):
        with patch.object(profile, "target_info", return_value={"architecture": "different"}):
            self.assertFalse(profile.current(self.receipt))

    def test_missing_flight_verification_prevents_reuse(self):
        self.receipt["verification"].pop()
        write_json(profile.RECEIPT, self.receipt)
        self.assertFalse(profile.current(self.receipt))


if __name__ == "__main__":
    unittest.main()
