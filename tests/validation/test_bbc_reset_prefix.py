"""Compare the production whole-machine reset path with the original model."""

import unittest

from tools.bbc import build, system96
from tools.bbc.reset_prefix import qualify


class ResetPrefixTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # The normal validation suite may begin with only the 32 MHz build.
        # Build the real 96 MHz machine once, retaining the qualifier's strict
        # rejection of stale binaries and independent-reset requirement.
        if not build.native_current(3):
            system96.main()

    def test_cold_reset_at_32_mhz(self):
        result = qualify(clock_multiplier=1)
        self.assertTrue(result["passed"])
        self.assertEqual(result["audio_samples"], 60)

    def test_cold_reset_at_96_mhz(self):
        result = qualify(clock_multiplier=3)
        self.assertTrue(result["passed"])
        self.assertEqual(result["master_clock_hz"], 96000000)


if __name__ == "__main__":
    unittest.main()
