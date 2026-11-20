"""Reject changes to the source parity and character-skip rules."""

import unittest

from tools.bbc.proofs.video_source_clock import guard_source


class VideoSourceClockTests(unittest.TestCase):
    SOURCE = """this.oddClock = false;
this.video.halfClock = !(val & 0x10);
this.oddClock = !this.oddClock;
if (this.halfClock && !this.oddClock) {
                continue;
            }
"""

    def test_guard_accepts_the_original_order(self):
        guard_source(self.SOURCE)

    def test_changed_initialization_skip_or_order_is_rejected(self):
        for text in (
            self.SOURCE.replace("oddClock = false", "oddClock = true"),
            self.SOURCE.replace("halfClock && !this.oddClock", "halfClock && this.oddClock"),
            self.SOURCE.replace("this.oddClock = !this.oddClock;", "")
            + "this.oddClock = !this.oddClock;",
        ):
            with self.subTest(text=text), self.assertRaises(ValueError):
                guard_source(text)


if __name__ == "__main__":
    unittest.main()
