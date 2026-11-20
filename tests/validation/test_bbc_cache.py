"""A fast replay must never reuse changed, missing or unverified boot state."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.bbc import boot_cache
from tools.validation.common import ROOT, digest, write_json


class BootCacheTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / "build")
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.source = self.directory / "source"
        self.source.write_bytes(b"original inputs")
        self.reference_source = self.directory / "reference-source"
        self.reference_source.write_bytes(b"original reference inputs")
        self.states = [
            self.directory / name
            for name in ["native.state", "native.ssd", "native.rgb", "reference.state"]
        ]
        for p in self.states:
            p.write_bytes(p.name.encode())
        self.receipt = self.directory / "receipt.json"
        self.addCleanup(patch.stopall)
        patch.object(boot_cache, "RECEIPT", self.receipt).start()
        patch.object(boot_cache, "STATES", self.states).start()
        patch.object(
            boot_cache, "BACKENDS", {"dut": self.states[:3], "reference": self.states[3:]}
        ).start()
        patch.object(
            boot_cache,
            "inputs",
            lambda backend: {
                "source": digest(self.source if backend == "dut" else self.reference_source),
                "boot_cycles": boot_cache.BOOT_CYCLES,
            },
        ).start()
        self.native = patch.object(boot_cache, "native_current", return_value=True).start()
        write_json(
            self.receipt,
            {
                "schema": 2,
                "backends": {
                    name: {
                        "inputs": boot_cache.inputs(name),
                        "states": boot_cache.state_hashes(name),
                        "independent_execution": "This machine executed its own boot",
                    }
                    for name in boot_cache.BACKENDS
                },
            },
        )

    def test_verified_unchanged_snapshots_can_be_reused(self):
        self.assertTrue(boot_cache.valid())

    def test_each_snapshot_mutation_invalidates_reuse(self):
        for p in self.states:
            original = p.read_bytes()
            p.write_bytes(original + b"changed")
            self.assertFalse(boot_cache.valid(), p.name)
            p.write_bytes(original)

    def test_missing_state_invalidates_reuse(self):
        self.states[-1].unlink()
        self.assertFalse(boot_cache.valid())

    def test_source_mutation_and_stale_native_build_invalidate_reuse(self):
        self.source.write_bytes(b"changed inputs")
        self.assertFalse(boot_cache.valid())
        self.assertTrue(boot_cache.valid("reference"))
        self.source.write_bytes(b"original inputs")
        self.native.return_value = False
        self.assertFalse(boot_cache.valid())
        self.assertTrue(boot_cache.valid("reference"))

    def test_reference_code_change_keeps_the_unchanged_dut_boot(self):
        self.reference_source.write_bytes(b"changed reference observer")
        self.assertFalse(boot_cache.valid("reference"))
        self.assertTrue(boot_cache.valid("dut"))

    def test_changed_boot_recipe_invalidates_both_backends(self):
        with patch.object(boot_cache, "BOOT_CYCLES", 1):
            self.assertFalse(boot_cache.valid())

    def test_invalid_or_unproven_receipt_invalidates_reuse(self):
        original = json.loads(self.receipt.read_text())
        write_json(self.receipt, {**original, "schema": 1})
        self.assertFalse(boot_cache.valid())
        original["backends"]["dut"]["independent_execution"] = ""
        write_json(self.receipt, original)
        self.assertFalse(boot_cache.valid())
        self.receipt.write_text("{broken")
        self.assertFalse(boot_cache.valid())


class BootRecipeTests(unittest.TestCase):
    def test_verified_compiler_optimization_preserves_source_bound_boot(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "build") as temporary:
            directory = Path(temporary)
            for name in ["boot.keys", "rom.hex", "disc.ssd"]:
                (directory / name).write_bytes(name.encode())
            native = {
                "generation": {"device": "original"},
                "runner": "capture",
                "compiler": "pinned",
                "verilator": "pinned",
            }
            with (
                patch.object(boot_cache, "BBC", directory),
                patch.object(boot_cache, "native_inputs", return_value=native),
            ):
                write_json(directory / "native-build.json", {"binary": "baseline"})
                baseline = boot_cache.inputs("dut")
                write_json(
                    directory / "native-build.json",
                    {"binary": "optimized", "optimization": "qualified"},
                )
                self.assertEqual(boot_cache.inputs("dut"), baseline)
                with patch.object(
                    boot_cache, "native_inputs", return_value={**native, "runner": "changed"}
                ):
                    self.assertNotEqual(boot_cache.inputs("dut"), baseline)


if __name__ == "__main__":
    unittest.main()
