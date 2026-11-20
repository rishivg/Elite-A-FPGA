"""Run both cold machines through the first keyboard interrupt and CPU return."""

import subprocess
import sys
import unittest

from tools.bbc import build, system96
from tools.bbc.boot_trace import qualify
from tools.validation.common import ROOT


class ColdKeyboardIrqTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not build.native_current():
            subprocess.run([sys.executable, "-m", "tools.bbc.build"], cwd=ROOT, check=True)
        if not build.native_current(3):
            system96.main()

    def test_exact_cold_keyboard_bus_at_32_mhz(self):
        result = qualify(cycles=35230000)
        self.assertTrue(result["passed"])
        self.assertFalse(result["full_boot"])
        self.assertEqual(result["ram_writes"]["reference_events"], 131944)

    def test_exact_cold_keyboard_bus_at_96_mhz(self):
        result = qualify(clock_multiplier=3, cycles=35230000)
        self.assertTrue(result["passed"])
        self.assertFalse(result["full_boot"])
        self.assertEqual(result["ram_writes"]["reference_events"], 131944)


if __name__ == "__main__":
    unittest.main()
