"""Reject source changes that invalidate the physical NMI sampling bridge."""

import unittest

from tools.bbc.proofs.cpu_reference.nmi_sampling import detector


class NmiSamplingSourceTests(unittest.TestCase):
    SOURCE = """uint64_t m6502_tick(m6502_t* c, uint64_t pins) {
        if (0 != ((pins & (pins ^ c->PINS)) & M6502_NMI)) {
            c->nmi_pip |= 0x100;
        }
        if (pins & M6502_RDY) {
            c->PINS = pins;
            c->irq_pip <<= 1;
            return pins;
        }
        c->PINS = pins;
        return pins;
    }
"""

    def test_exact_maintained_detector_is_retained(self):
        self.assertIn("c->nmi_pip |= 0x100;", detector(self.SOURCE))

    def test_new_stored_pin_consumer_or_changed_capture_requires_new_proof(self):
        for before, after in (
            ("c->nmi_pip |= 0x100;", "c->nmi_pip |= 0x200;"),
            ("c->irq_pip <<= 1;", "c->irq_pip <<= 1; c->AD = c->PINS;"),
            ("c->PINS = pins;", "c->PINS |= pins;"),
        ):
            with self.subTest(before=before), self.assertRaises(ValueError):
                detector(self.SOURCE.replace(before, after))


if __name__ == "__main__":
    unittest.main()
