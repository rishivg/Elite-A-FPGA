"""Reject source changes that invalidate the horizontal-sync projection."""

import unittest

from tools.bbc.proofs.hsync_source_history import source_guard
from tools.validation.common import source_root


class HsyncSourceGuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = (source_root("jsbeeb") / "src/video.js").read_text()

    def test_changed_width_mask_counter_wrap_or_branch_priority_is_rejected(self):
        for before, after in (
            ("this.video.hpulseWidth = val & 0x0f;", "this.video.hpulseWidth = val & 0x07;"),
            ("(this.hpulseCounter + 1) & 0x0f", "(this.hpulseCounter + 1) & 0x07"),
            (
                "} else if (this.hpulseCounter === (this.regs[3] & 0x0f))",
                "} if (this.hpulseCounter === (this.regs[3] & 0x0f))",
            ),
        ):
            self.assertIn(before, self.source)
            with self.subTest(before=before), self.assertRaises(ValueError):
                source_guard(self.source.replace(before, after))

    def test_reversed_termination_and_initiation_is_rejected(self):
        statement = "if (this.inHSync && this.handleHSync()) this.flyback();"
        self.assertEqual(self.source.count(statement), 1)
        changed = self.source.replace(statement, "") + "\n" + statement
        with self.assertRaisesRegex(ValueError, "order"):
            source_guard(changed)


if __name__ == "__main__":
    unittest.main()
