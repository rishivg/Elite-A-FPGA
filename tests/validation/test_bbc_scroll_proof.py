"""A local scroll proof must bind to the actual system VIA and video ports."""

import unittest

from tools.bbc.scroll_proof import parent_binding

PARENT = """module Machine(input clk, reset);
 Via6522 sys (
 .clk(clk), .reset(reset), .io_busPortB(bus[7:0]), .io_portBIn(external[7:0])
 );
 BbcSystemPins systemPins (
 .clk(clk), .reset(reset), .io_busPortB(bus[7:0]), .io_scroll(scroll[1:0])
 );
 BbcVideo video (
 .io_scroll(scroll[1:0])
 );
endmodule
"""


class ScrollProofTests(unittest.TestCase):
    def test_parent_observation_preserves_the_original_connections(self):
        observed, connections = parent_binding(PARENT, "Machine")
        self.assertEqual(connections["via_bus_port_b"], "bus[7:0]")
        self.assertEqual(connections["video_scroll"], "scroll[1:0]")
        self.assertEqual(connections["via_port_input"], "external[7:0]")
        self.assertIn("assign proof_portInput = external[7:0];", observed)
        self.assertEqual(
            observed.replace(
                "wire [7:0] proof_portInput;\nassign proof_portInput = external[7:0];\n", ""
            ),
            PARENT,
        )

    def test_disconnected_bus_scroll_clock_reset_or_missing_child_is_rejected(self):
        for broken in (
            PARENT.replace(".io_busPortB(bus[7:0])", ".io_busPortB(other[7:0])", 1),
            PARENT.replace(".io_scroll(scroll[1:0])", ".io_scroll(other[1:0])", 1),
            PARENT.replace(".clk(clk)", ".clk(other)", 1),
            PARENT.replace(".reset(reset)", ".reset(other)", 1),
            PARENT.replace("BbcVideo video", "BbcVideo disconnected"),
            PARENT.replace(", .io_portBIn(external[7:0])", ""),
        ):
            with self.subTest(broken=broken), self.assertRaises(ValueError):
                parent_binding(broken, "Machine")


if __name__ == "__main__":
    unittest.main()
