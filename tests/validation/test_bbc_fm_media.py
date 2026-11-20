"""Check every standard media cell against the original disc encoder."""

import subprocess
import tempfile
import unittest
from pathlib import Path

from tools.bbc import fm_media, media
from tools.validation.common import ROOT, source_root


class FmMediaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.native_directory = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.native_directory.cleanup)
        cls.native = Path(cls.native_directory.name) / "fm-media"
        subprocess.run(
            [
                "c++",
                "-O2",
                "-std=c++17",
                "-Wall",
                "-Wextra",
                "-Werror",
                str(ROOT / "sim/native/bbc_fm_media_test.cpp"),
                "-o",
                str(cls.native),
            ],
            check=True,
            capture_output=True,
            timeout=60,
        )

    def test_released_and_commander_media_match_all_original_clock_data_cells(self):
        released = (
            source_root("elite_a") / "5-compiled-game-discs/elite-a-released.ssd"
        ).read_bytes()
        for image in (released, media.image(released, "rich-leesti")):
            with self.subTest(bytes=len(image)), tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary)
                ssd, original = directory / "input.ssd", directory / "original.fm"
                ssd.write_bytes(image)
                subprocess.run(
                    [
                        "node",
                        str(ROOT / "tools/bbc/reference_fm_media.mjs"),
                        str(source_root("jsbeeb")),
                        str(ssd),
                        str(original),
                    ],
                    check=True,
                    capture_output=True,
                    timeout=30,
                )
                actual = fm_media.encode_standard_ssd(image)
                self.assertEqual(actual, original.read_bytes())
                self.assertEqual(len(actual), 500000)
                self.assertEqual(
                    fm_media.project_standard_payloads(actual), image.ljust(204800, b"\0")
                )
                native_raw, native_flat = directory / "native.fm", directory / "native.ssd"
                result = subprocess.run(
                    [str(self.native), str(ssd), str(native_raw), str(native_flat)],
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                self.assertIn("all 65536 clock/data words survive persistence", result.stdout)
                self.assertEqual(native_raw.read_bytes(), original.read_bytes())
                self.assertEqual(native_flat.read_bytes(), image.ljust(204800, b"\0"))

    def test_projection_discards_clock_damage_and_cannot_replace_raw_medium(self):
        image = bytes(range(256))
        raw = fm_media.encode_standard_ssd(image)
        damaged = bytearray(raw)
        damaged[2 * fm_media.FIRST_PAYLOAD_CELL + 1] ^= 1
        self.assertNotEqual(raw, damaged)
        self.assertEqual(
            fm_media.project_standard_payloads(raw), fm_media.project_standard_payloads(damaged)
        )

    def test_invalid_input_sizes_are_rejected(self):
        for image in (b"", bytes(257), bytes(204801)):
            with self.subTest(bytes=len(image)), self.assertRaises(ValueError):
                fm_media.encode_standard_ssd(image)
        for cells in (b"", bytes(499999), bytes(500001)):
            with self.subTest(bytes=len(cells)), self.assertRaises(ValueError):
                fm_media.project_standard_payloads(cells)
