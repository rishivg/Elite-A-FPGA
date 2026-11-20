"""Reject unsupported source changes before a CRTC history query executes."""

import json
import subprocess
import unittest

from tools.validation.common import ROOT, source_root


class CrtcSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = (source_root("jsbeeb") / "src/video.js").read_text()

    def lower(self, source):
        return subprocess.run(
            [
                "node",
                "--input-type=module",
                "-e",
                "import fs from 'node:fs';"
                "import {lower} from './tools/bbc/crtc_source_projection.mjs';"
                "console.log(JSON.stringify(lower(JSON.parse(fs.readFileSync(0,'utf8'))).projection));",
            ],
            cwd=ROOT,
            input=json.dumps(source),
            text=True,
            capture_output=True,
            timeout=10,
        )

    def test_original_projection_has_complete_declared_domain(self):
        result = self.lower(self.source)
        self.assertEqual(result.returncode, 0, result.stderr)
        projection = json.loads(result.stdout)
        self.assertEqual(projection["width"], 90)
        self.assertEqual(len(projection["fields"]), 23)
        self.assertEqual(len({name for name, _, _ in projection["fields"]}), 23)

    def test_unsupported_retained_operations_are_rejected(self):
        for old, new in (
            ("this.scanlineCounter + 2", "this.scanlineCounter * 2"),
            (
                "this.vertCounter = (this.vertCounter + 1)",
                "this.vertCounter += (this.vertCounter + 1)",
            ),
            ("this.endOfCharacterLine();", "this.unmodelledSideEffect();"),
        ):
            with self.subTest(change=old):
                self.assertIn(old, self.source)
                result = self.lower(self.source.replace(old, new))
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("Unsupported", result.stderr)

    def test_changed_cursor_table_and_display_bit_are_rejected(self):
        for old, new in (
            ("[0x00, 0x00, 0x08, 0x10]", "[0x00, 0x00, 0x04, 0x10]"),
            ("export const VDISPENABLE = 1 << 0;", "export const VDISPENABLE = 1 << 1;"),
        ):
            with self.subTest(change=old):
                self.assertIn(old, self.source)
                result = self.lower(self.source.replace(old, new))
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("Changed original", result.stderr)

    def test_duplicate_or_missing_helper_is_rejected(self):
        for altered, message in (
            (
                self.source.replace(
                    "    endOfFrame() {", "    endOfFrame() {}\n    endOfFrame() {"
                ),
                "Duplicate",
            ),
            (self.source.replace("    endOfFrame() {", "    differentFrameMethod() {"), "Missing"),
        ):
            result = self.lower(altered)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(message, result.stderr)


if __name__ == "__main__":
    unittest.main()
