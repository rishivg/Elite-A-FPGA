"""Accepted VIA GPIO, keyboard and sound bus integration qualification."""

import unittest

from tools.bbc.system_probe import qualify, qualify_keyboard, qualify_keyboard_edges


class SystemPinsTests(unittest.TestCase):
    def test_key_irq_visibility_at_every_master_phase(self):
        result = qualify_keyboard_edges()
        self.assertTrue(result["passed"])
        self.assertEqual(result["cases"], 8960)

    def test_physical_keys_latch_and_sound_acceptance(self):
        result = qualify()
        self.assertTrue(result["passed"])
        self.assertEqual(result["bus_observations"], 148)
        self.assertEqual(result["sound_events"], 44)

    def test_gpio_ca2_interrupt_at_cpu_edge_and_acknowledgement(self):
        result = qualify_keyboard()
        self.assertTrue(result["passed"])
        self.assertEqual(result["bus_observations"], 112)


if __name__ == "__main__":
    unittest.main()
