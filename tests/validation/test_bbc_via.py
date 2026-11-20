"""Independent original-device qualification of the generated VIA RTL."""

import unittest

from tools.bbc.via_probe import qualify


class ViaTimerTests(unittest.TestCase):
    def test_counter_readback_relatch_interrupts_and_pb7(self):
        result = qualify()
        self.assertTrue(result["passed"])
        self.assertEqual(result["observations"], 6552)

    def test_cpu_bus_reads_and_acknowledgement_at_timer_edges(self):
        result = qualify(bus=True)
        self.assertTrue(result["passed"])
        self.assertEqual(result["observations"], 6552)

    def test_control_edges_latches_and_simultaneous_acknowledgement(self):
        result = qualify(edges=True)
        self.assertTrue(result["passed"])
        self.assertEqual(result["observations"], 2944)


if __name__ == "__main__":
    unittest.main()
