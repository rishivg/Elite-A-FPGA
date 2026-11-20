"""Commander input media must leave all released game and ship bytes intact."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.bbc import boot_cache, media
from tools.bbc.capture import native_environment
from tools.bbc.run import compile_selected, verified_boots
from tools.validation.common import ROOT, source_root
from tools.validation.setup import disc_files


class CommanderMediaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.released = (
            source_root("elite_a") / "5-compiled-game-discs/elite-a-released.ssd"
        ).read_bytes()

    def test_normal_dfs_file_changes_only_declared_commander_fields(self):
        original = disc_files(self.released)
        made = media.image(self.released, "rich-leesti")
        files = disc_files(made)
        self.assertEqual(len(made), 204800)
        self.assertEqual(set(files), set(original) - {"B.CONVERT"} | {"E.RICH"})
        for name, data in original.items():
            if name != "B.CONVERT":
                self.assertEqual(files[name], data, name)
        data = files["E.RICH"]
        self.assertEqual(len(data), 256)
        self.assertEqual(int.from_bytes(data[9:13], "big"), 100000000)
        self.assertEqual(data[1:3], bytes([13, 186]))
        source = original["2.T"]
        marker = source.index(b"NEWCOME\r")
        default = source[marker + 8 : marker + 84]
        self.assertEqual(media.checksum(default), 88)
        self.assertEqual(data[75], media.checksum(data))
        self.assertEqual(data[74], data[75] ^ 169)
        for index in set(range(76)) - {1, 2, 9, 10, 11, 12, 74, 75}:
            self.assertEqual(data[index], default[index], index)
        self.assertEqual(data[76:], bytes(180))
        broken = bytearray(data)
        broken[10] ^= 1
        self.assertNotEqual(media.checksum(broken), broken[75])

    def test_invalid_media_and_presets_fail_before_boot(self):
        for data in [b"", bytes(204801), bytes(512)]:
            with self.subTest(length=len(data)), self.assertRaises(ValueError):
                media.image(data, "rich-leesti")
        with self.assertRaisesRegex(ValueError, "Unknown commander"):
            media.image(self.released, "unknown")
        with self.assertRaises(ValueError):
            compile_selected(
                {"actions": {}, "scenarios": [{"name": "invalid", "media_fixture": "unknown"}]}
            )
        with self.assertRaises(ValueError):
            media.checksum(bytes(75))

    def test_galaxy_wrap_input_is_a_valid_commander_not_a_machine_snapshot(self):
        data = disc_files(media.image(self.released, "galaxy-eight"))["E.RICH"]
        self.assertEqual(data[1:3], bytes([1, 45]))
        self.assertEqual(data[3:9], bytes([37, 45, 36, 1, 169, 219]))
        self.assertEqual(data[15], 7)
        self.assertEqual(data[45], 255)
        self.assertEqual(data[47:49], (5250).to_bytes(2, "little"))
        self.assertEqual(data[49:51], bytes([6, 143]))
        self.assertEqual(data[52], 128)
        self.assertEqual(data[75], media.checksum(data))
        self.assertEqual(data[74], data[75] ^ 169)

    def test_ambient_input_media_cannot_change_a_capture(self):
        with patch.dict(
            os.environ, {"BBC_DISC_IMAGE": "unexpected.ssd", "BBC_LOAD_STATE": "unexpected.state"}
        ):
            self.assertNotIn("BBC_DISC_IMAGE", native_environment())
            self.assertNotIn("BBC_LOAD_STATE", native_environment())
            self.assertEqual(
                native_environment(disc=Path("declared.ssd"))["BBC_DISC_IMAGE"], "declared.ssd"
            )
            with self.assertRaisesRegex(ValueError, "own saved disc"):
                native_environment(load="own.state", disc="different.ssd")

    def test_debug_trace_flags_require_explicit_capture_options(self):
        with patch.dict(
            os.environ, {"BBC_BUS_TRACE": "1", "BBC_RAM_TRACE": "1", "BBC_CAPTURE_PREFIX": "1"}
        ):
            clean = native_environment()
            self.assertNotIn("BBC_BUS_TRACE", clean)
            self.assertNotIn("BBC_RAM_TRACE", clean)
            self.assertNotIn("BBC_CAPTURE_PREFIX", clean)
            selected = native_environment(ram_trace=True)
            self.assertEqual(selected["BBC_RAM_TRACE"], "1")
            self.assertNotIn("BBC_BUS_TRACE", selected)

    def test_prefix_scope_is_explicit_and_cannot_be_a_game_snapshot(self):
        self.assertEqual(native_environment(cold_prefix=True)["BBC_CAPTURE_PREFIX"], "1")
        for options in [{"load": "own.state"}, {"save": "own.state"}]:
            with self.assertRaisesRegex(ValueError, "cannot restore or save"):
                native_environment(cold_prefix=True, **options)

    def test_media_cache_separation_and_receipt_mutation(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "build") as temporary:
            directory = Path(temporary)
            a, b = directory / "a.ssd", directory / "b.ssd"
            a.write_bytes(b"first declared disc")
            b.write_bytes(b"second declared disc")
            options = dict(boot_cache.REFERENCE_OPTIONS)
            with patch.object(boot_cache, "BBC", directory):
                first, second = [boot_cache.media_profile(p, options) for p in [a, b]]
            self.assertNotEqual(first.receipt, second.receipt)
            self.assertNotEqual(first.backends["dut"], second.backends["dut"])
            self.assertNotEqual(first.backends["dut"], boot_cache.BACKENDS["dut"])
            for paths in first.backends.values():
                for path in paths:
                    path.write_bytes(b"own executed state")
            current = {"media": "verified-content"}
            with (
                patch.object(boot_cache, "inputs", return_value=current),
                patch.object(boot_cache, "native_current", return_value=True),
            ):
                receipt = {
                    "schema": 2,
                    "backends": {
                        name: {
                            "inputs": current,
                            "states": boot_cache.state_hashes(name, first),
                            "independent_execution": True,
                        }
                        for name in first.backends
                    },
                }
                first.receipt.write_text(json.dumps(receipt))
                self.assertTrue(boot_cache.valid(context=first))
                self.assertFalse(boot_cache.valid(context=second))
                first.backends["dut"][1].write_bytes(b"different saved disc")
                self.assertFalse(boot_cache.valid(context=first))

    def test_final_guard_detects_changed_or_missing_selected_boots(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "build") as temporary:
            directory = Path(temporary)
            state = directory / "own.state"
            state.write_bytes(b"verified own-machine boot")
            context = boot_cache.Profile(
                directory / "input.ssd", {"dut": [state]}, directory / "receipt.json", {}
            )
            from tools.validation.common import digest

            contexts = {"fixture": context}
            snapshots = {"fixture": {"hashes": {str(state): digest(state)}}}
            with patch.object(boot_cache, "valid", return_value=True):
                self.assertTrue(verified_boots(contexts, snapshots))
                state.write_bytes(b"replaced during capture")
                self.assertFalse(verified_boots(contexts, snapshots))
                state.unlink()
                self.assertFalse(verified_boots(contexts, snapshots))
