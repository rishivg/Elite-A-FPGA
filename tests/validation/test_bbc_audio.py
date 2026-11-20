"""Compare the production recorder with independently maintained sound code."""

import unittest

from tools.bbc.audio_probe import qualify, qualify_bus, qualify_generators


class AudioIntervalTests(unittest.TestCase):
    def test_linked_zero_noise_through_the_32_mhz_sound_bus(self):
        result = qualify_bus(linked_zero=True)
        self.assertTrue(result["passed"])
        self.assertEqual(result["captures"]["writes"]["events"], 13)
        self.assertEqual(result["captures"]["pins"]["samples"], 8000)

    def test_linked_zero_noise_through_the_96_mhz_sound_bus(self):
        result = qualify_bus(clock_multiplier=3, linked_zero=True)
        self.assertTrue(result["passed"])
        self.assertEqual(result["captures"]["writes"]["events"], 13)
        self.assertEqual(result["captures"]["pins"]["samples"], 8000)

    def test_volume_writes_across_sample_boundaries(self):
        result = qualify()
        self.assertTrue(result["passed"])
        self.assertEqual(result["samples"], 16)

    def test_tone_noise_generators_and_control_rewrites(self):
        result = qualify_generators()
        self.assertTrue(result["passed"])
        self.assertEqual(result["captures"]["pins"]["samples"], 8000)

    def test_sound_bus_latest_byte_and_updates_while_enabled(self):
        result = qualify_bus()
        self.assertTrue(result["passed"])
        self.assertEqual(result["captures"]["writes"]["events"], 27)

    def test_sound_bus_acceptance_and_samples_at_96_mhz(self):
        result = qualify_bus(clock_multiplier=3)
        self.assertTrue(result["passed"])
        self.assertEqual(result["master_clock_hz"], 96000000)
        self.assertEqual(result["captures"]["writes"]["events"], 27)
        self.assertEqual(result["captures"]["levels"]["samples"], 8000)
        self.assertEqual(result["captures"]["pins"]["samples"], 8000)

    def test_model_b_initial_sound_window_at_32_mhz(self):
        result = qualify_bus(reset_only=True)
        self.assertTrue(result["passed"])
        self.assertEqual(result["captures"]["writes"]["events"], 1)
        self.assertEqual(result["captures"]["pins"]["samples"], 8000)

    def test_model_b_initial_sound_window_at_96_mhz(self):
        result = qualify_bus(clock_multiplier=3, reset_only=True)
        self.assertTrue(result["passed"])
        self.assertEqual(result["captures"]["writes"]["events"], 1)
        self.assertEqual(result["captures"]["pins"]["samples"], 8000)


if __name__ == "__main__":
    unittest.main()
