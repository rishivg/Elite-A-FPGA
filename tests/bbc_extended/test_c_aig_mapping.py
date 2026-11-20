"""An independent C/AIG check needs complete, unambiguous port mappings."""

import unittest

from tools.bbc.proofs.cpu_reference import aig_c, independent_translation


def identity():
    inputs = [str(2 * n) for n in range(1, 321)]
    outputs = [*inputs, "0"]
    labels = [f"i{b} state_in[{b}]" for b in range(256)]
    labels += [f"i{256 + b} pins_in[{b}]" for b in range(64)]
    labels += [f"o{b} state_out[{b}]" for b in range(256)]
    labels += [f"o{256 + b} pins_out[{b}]" for b in range(64)]
    return (
        "\n".join(
            ["aag 320 320 0 321 0", *inputs, *outputs, *labels, "o320 trap", "c", "test fixture"]
        )
        + "\n"
    )


class AigMappingTests(unittest.TestCase):
    def test_cbmc_success_requires_every_comparison(self):
        names = ["trap", "returned_pins", *("state_" + n for n, _ in aig_c.FIELDS)]
        valid = "\n".join(f"[assertion.{i}] line 1 {n}: SUCCESS" for i, n in enumerate(names))
        valid += "\n** 0 of 20 failed\nVERIFICATION SUCCESSFUL\n"
        self.assertTrue(independent_translation.verified_cbmc(valid))
        self.assertFalse(independent_translation.verified_cbmc("VERIFICATION SUCCESSFUL"))
        self.assertFalse(
            independent_translation.verified_cbmc(
                valid.replace("state_PINS: SUCCESS", "state_PINS: FAILURE")
            )
        )
        self.assertFalse(
            independent_translation.verified_cbmc(
                valid.replace("state_AD: SUCCESS", "other: SUCCESS")
            )
        )

    def test_complete_port_bit_coverage_and_wire_format(self):
        maximum, gates, outputs, ports = aig_c.parse(identity())
        self.assertEqual((maximum, len(gates), len(outputs)), (320, 0, 321))
        self.assertEqual(len(ports["i"]), 320)
        self.assertEqual(len(ports["o"]), 321)
        code = aig_c.generate(identity())
        self.assertIn("input->IR", code)
        self.assertIn("input->io_pins", code)
        self.assertIn("result.trap=0;", code)

    def test_missing_duplicate_and_wrong_port_symbols_are_rejected(self):
        text = identity()
        for broken in (
            text.replace("i319 pins_in[63]\n", ""),
            text.replace("i319 pins_in[63]", "i319 pins_in[62]"),
            text.replace("o320 trap", "o320 different"),
            text.replace("o255 state_out[255]", "o255 state_out[256]"),
        ):
            with self.subTest(broken=broken[-100:]), self.assertRaises(ValueError):
                aig_c.parse(broken)

    def test_inverted_literals_and_undefined_outputs(self):
        self.assertEqual(aig_c.literal(3), "!node_1")
        with self.assertRaises(ValueError):
            aig_c.parse(identity().replace("\n2\n4\n", "\n2\n642\n", 1))


if __name__ == "__main__":
    unittest.main()
