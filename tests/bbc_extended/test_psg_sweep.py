"""Extended independent PSG checks, kept outside the fast validation suite."""

import unittest

from tools.bbc.psg_sweep import qualify


class PsgSweepTests(unittest.TestCase):
    def test_noise_control_latch_data_and_seed_at_tick_boundaries(self):
        result = qualify("noise-controls")
        self.assertTrue(result["passed"])
        self.assertEqual(result["reference_coverage"]["noise_controls"], 8)
        self.assertEqual(result["captures"]["pins"]["samples"], 65536)

    def test_tone_period_writes_at_generator_tick_boundaries(self):
        result = qualify("clock-writes")
        self.assertTrue(result["passed"])
        self.assertEqual(result["captures"]["pins"]["samples"], 65536)

    def test_linked_zero_entry_exit_at_half_tick_boundaries(self):
        result = qualify("linked-zero")
        self.assertTrue(result["passed"])
        self.assertEqual(result["captures"]["pins"]["samples"], 131072)
        self.assertGreater(result["reference_coverage"]["white_states"], 0)

    def test_all_tone_periods_and_attenuations(self):
        result = qualify("tones")
        self.assertTrue(result["passed"])
        self.assertEqual(result["reference_coverage"]["tone_periods"], [1024, 1024, 1024])
        self.assertEqual(result["captures"]["pins"]["samples"], 4214776)

    def test_complete_white_and_periodic_noise_cycles(self):
        result = qualify("noise")
        self.assertTrue(result["passed"])
        self.assertEqual(result["reference_coverage"]["white_states"], 32767)
        self.assertEqual(result["reference_coverage"]["periodic_states"], 15)
        self.assertEqual(result["captures"]["pins"]["samples"], 2105344)

    def test_both_linked_noise_modes_at_every_divider(self):
        result = qualify("linked")
        self.assertTrue(result["passed"])
        self.assertEqual(result["reference_coverage"]["linked_periods"], [1024, 1024])
        self.assertEqual(result["captures"]["pins"]["samples"], 13658376)


if __name__ == "__main__":
    unittest.main()
