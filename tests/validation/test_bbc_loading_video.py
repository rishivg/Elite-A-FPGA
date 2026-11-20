"""Reject component changes hidden by wrapper or ROM filename differences."""

import gzip
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.bbc import boot_cache, build
from tools.bbc.loading_video import (
    bitmap_disagreement,
    capture_endpoint,
    machine_binding,
    original_capture,
)
from tools.validation.common import ROOT, write_json


class LoadingVideoBindingTests(unittest.TestCase):
    def test_only_saved_full_boots_can_advance_to_a_real_field(self):
        sync = {
            "final_cycle": 36020173,
            "transitions": [{"cycle": 36020173, "level": True, "control": 4}],
        }
        self.assertEqual(capture_endpoint(576000000, {"cycles": 36020173}, sync), 36020173)
        with self.assertRaisesRegex(ValueError, "Cold prefix"):
            capture_endpoint(576000032, {"cycles": 36020173}, sync)
        sync["transitions"][-1]["level"] = False
        with self.assertRaisesRegex(ValueError, "VSYNC rise"):
            capture_endpoint(576000000, {"cycles": 36020173}, sync)
        with self.assertRaisesRegex(ValueError, "endpoints disagree"):
            capture_endpoint(576000000, {"cycles": 36020172}, sync)

    def fixture(self, directory):
        modules = [
            "Crtc6845",
            "Saa5050Cell",
            "TeletextFont",
            "TeletextAttributes",
            "TeletextGlyphRow",
            "BbcVideoAddress",
        ]
        child = "".join(f"module {name}();\nendmodule\n" for name in modules)
        (directory / "machine.bin").write_text("1010\n")
        (directory / "component.bin").write_text("1010\n")
        (directory / "BbcMachine96.v").write_text(
            'module BbcVideo();\ninitial $readmemb("machine.bin", font);\nendmodule\n' + child
        )
        (directory / "BbcVideo96.v").write_text(
            'module BbcVideo96();\ninitial $readmemb("component.bin", font);\nendmodule\n' + child
        )

    def test_changed_rom_contents_cannot_hide_behind_filename_normalization(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            self.fixture(directory)
            with patch("tools.bbc.loading_video.video96.DIRECTORY", directory):
                self.assertEqual(len(machine_binding()), 7)
                (directory / "component.bin").write_text("1011\n")
                with self.assertRaisesRegex(ValueError, "Whole-machine video differs"):
                    machine_binding()

    def test_changed_child_logic_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            self.fixture(directory)
            path = directory / "BbcVideo96.v"
            path.write_text(
                path.read_text().replace(
                    "module Crtc6845();", "module Crtc6845();\nwire changed = 1'b1;"
                )
            )
            with (
                patch("tools.bbc.loading_video.video96.DIRECTORY", directory),
                self.assertRaisesRegex(ValueError, "Crtc6845"),
            ):
                machine_binding()


class OriginalLoadingReuseTests(unittest.TestCase):
    def fixture(self, directory):
        reference = directory / "reference"
        reference.mkdir()
        keys = directory / "keys"
        keys.write_text("")
        configuration = directory / "reference.json"
        write_json(
            configuration, {**boot_cache.REFERENCE_OPTIONS, "cycles": 32000000, "keys": str(keys)}
        )
        write_json(reference / "summary.json", {"cycles": 2000000})
        write_json(reference / "raw-vsync.json", {"final_cycle": 2000000})
        write_json(
            reference / "video-pins.json", {"count": 1999984, "uncompressed_bytes": 1999984 * 20}
        )
        observers = [directory / f"observer{i}.mjs" for i in range(4)]
        for observer in observers:
            observer.write_text("// test observer\n")
        inputs = {"test_reference": "unchanged"}
        receipt = directory / "result.json"
        write_json(
            receipt,
            {
                "passed": False,
                "checks": {"unchanged_inputs": True},
                "requested_cycles": 32000000,
                "inputs": {
                    "reference": inputs,
                    "files": build.hashes([configuration, keys, *observers]),
                },
                "artifacts": build.hashes(reference.glob("*")),
            },
        )
        return receipt, keys, observers, inputs

    def test_original_observations_do_not_inherit_a_prior_dut_pass(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "build") as temporary:
            directory = Path(temporary)
            receipt, keys, observers, inputs = self.fixture(directory)
            with patch("tools.bbc.loading_video.boot_cache.inputs", return_value=inputs):
                reference, producer = original_capture(receipt, 32000000, keys, observers)
                self.assertEqual(reference, directory / "reference")
                self.assertNotIn("passed", producer)
                (reference / "summary.json").write_text('{"cycles":1999999}')
                with self.assertRaisesRegex(ValueError, "observation bytes changed"):
                    original_capture(receipt, 32000000, keys, observers)

    def test_changed_original_observer_blocks_reuse(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "build") as temporary:
            receipt, keys, observers, inputs = self.fixture(Path(temporary))
            observers[0].write_text("// modified producer\n")
            with (
                patch("tools.bbc.loading_video.boot_cache.inputs", return_value=inputs),
                self.assertRaisesRegex(ValueError, "producer changed"),
            ):
                original_capture(receipt, 32000000, keys, observers)


class BitmapModelDisagreementTests(unittest.TestCase):
    def compare(self, javascript, chip):
        with tempfile.TemporaryDirectory() as temporary:
            files = [Path(temporary) / name for name in ("javascript.gz", "chip.gz")]
            for path, data in zip(files, (javascript, chip)):
                with gzip.open(path, "wb") as stream:
                    stream.write(data)
            return bitmap_disagreement(*files)

    def test_every_disagreement_is_counted_without_claiming_a_dut_pass(self):
        report = self.compare(bytes(1030), bytes([7]) * 1030)
        self.assertFalse(report["qualified"])
        self.assertEqual(report["different_dots"], 1030)
        self.assertEqual(report["all_dots"], 1030)
        self.assertEqual(len(report["differences"]), 1024)
        self.assertEqual(report["differences"][8]["cpu_slot"], 17)
        self.assertEqual(report["differences"][8]["physical_clock"], 17 * 48 + 5)

    def test_truncation_of_either_original_is_rejected(self):
        for javascript, chip in ((bytes(9), bytes(8)), (bytes(8), bytes(9))):
            with self.assertRaisesRegex(ValueError, "lengths differ"):
                self.compare(javascript, chip)


if __name__ == "__main__":
    unittest.main()
