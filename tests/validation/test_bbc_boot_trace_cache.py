"""Partial audits and changed snapshots must not become playable own boots."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.bbc import boot_cache, boot_trace_cache, build
from tools.bbc.boot_trace_cache import retain_dut
from tools.validation.common import ROOT, write_json


class AuditedBootCacheTests(unittest.TestCase):
    def test_failed_and_prefix_audits_are_rejected_before_any_cache_change(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "build") as temporary:
            receipt = Path(temporary) / "result.json"
            for passed, full in ((False, True), (True, False)):
                write_json(
                    receipt,
                    {
                        "passed": passed,
                        "full_boot": full,
                        "requested_cycles": boot_cache.BOOT_CYCLES,
                        "checks": {"complete": True},
                    },
                )
                with patch("tools.bbc.boot_trace_cache.boot_cache.configure") as configure:
                    with self.assertRaisesRegex(ValueError, "completed passing"):
                        retain_dut(receipt)
                    configure.assert_not_called()

    def test_changed_own_state_is_rejected_before_publishing(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "build") as temporary:
            directory = Path(temporary)
            states = [
                directory / ("dut-own.state" + suffix) for suffix in ("", ".fm", ".ssd", ".rgb")
            ]
            for state in states:
                state.write_bytes(b"independent original bytes")
            inputs = {"files": {}, "native": {"model": "same"}}
            receipt = directory / "result.json"
            write_json(
                receipt,
                {
                    "passed": True,
                    "full_boot": True,
                    "requested_cycles": boot_cache.BOOT_CYCLES,
                    "master_clock_hz": 32000000,
                    "checks": {"complete": True},
                    "inputs": {"dut": inputs["native"], "reference": {"files": {}}},
                    "artifacts": build.hashes(states),
                },
            )
            states[0].write_bytes(b"changed snapshot")
            with (
                patch("tools.bbc.boot_trace_cache.boot_cache.configure"),
                patch("tools.bbc.boot_trace_cache.boot_cache.inputs", return_value=inputs),
                patch("tools.bbc.boot_trace_cache.build.native_current", return_value=True),
                patch("tools.bbc.boot_trace_cache.boot_cache.seal") as seal,
            ):
                with self.assertRaisesRegex(ValueError, "state is missing or changed"):
                    retain_dut(receipt)
                seal.assert_not_called()

    def test_audited_boot_installs_all_native_sidecars(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "build") as temporary:
            directory = Path(temporary)
            source, cache = directory / "source", directory / "cache"
            (source / "dut").mkdir(parents=True)
            cache.mkdir()
            (source / "dut/boot.ram").write_bytes(b"independent RAM")
            states = boot_cache.dut_states(source / "dut-own.state")
            targets = boot_cache.dut_states(cache / "boot.state")
            for index, path in enumerate(states):
                path.write_bytes(bytes([index]))
            inputs = {"files": {}, "native": {"model": "same"}}
            receipt = source / "result.json"
            write_json(
                receipt,
                {
                    "passed": True,
                    "full_boot": True,
                    "requested_cycles": boot_cache.BOOT_CYCLES,
                    "master_clock_hz": 32000000,
                    "checks": {"complete": True},
                    "inputs": {"dut": inputs["native"], "reference": {"files": {}}, "runner": {}},
                    "artifacts": build.hashes(states),
                },
            )
            status = {
                "commander_name": "NEWCOME", "credits_tenths": 5000,
                "fuel_tenths_ly": 60, "view": 8, "docked": True,
            }
            with (
                patch.object(build, "BBC", cache),
                patch.object(build, "native_current", return_value=True),
                patch.object(boot_cache, "configure"),
                patch.object(boot_cache, "inputs", return_value=inputs),
                patch.object(boot_cache, "backends", return_value={"dut": targets}),
                patch.object(boot_cache, "receipt_path", return_value=cache / "boot-cache.json"),
                patch.object(boot_cache, "valid", return_value=True),
                patch.object(boot_cache, "state_hashes", return_value={}),
                patch.object(boot_cache, "seal") as seal,
                patch.object(boot_trace_cache, "game_state", return_value=status),
            ):
                self.assertTrue(retain_dut(receipt)["retained"])
            seal.assert_called_once()
            self.assertEqual([path.read_bytes() for path in targets], [p.read_bytes() for p in states])


if __name__ == "__main__":
    unittest.main()
