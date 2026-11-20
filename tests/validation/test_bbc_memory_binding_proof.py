"""Observe actual memory ports and reject a detached CPU or incomplete latch state."""

import unittest

from tests.validation.test_bbc_bus_devices import PARENT
from tools.bbc.memory_binding_proof import observed

MEMORY_PARENT = PARENT.replace(
    "BbcCpu cpu (\n.irq(cpuIrq)",
    "BbcCpu cpu (\n.clk(clk), .reset(reset), .enable(cpuEnable), .read_data(cpuRead), .irq(cpuIrq)",
).replace(
    "endmodule",
    """
reg [3:0] romSelect;
reg [1:0] memorySelect;
reg romPresent;
reg [7:0] peripheralData;
BbcRam ram (
.clk(clk), .reset(reset), .io_cpuEnable(ramEnable), .io_cpuAddress(ramAddress),
.io_cpuWrite(ramWrite), .io_cpuData(ramData), .io_cpuReadData(ramResult)
);
endmodule
""",
)


class MemoryBindingProofTests(unittest.TestCase):
    def test_read_and_request_properties_follow_actual_ports(self):
        result, _ = observed(MEMORY_PARENT, "Machine")
        self.assertIn("(cpuRead)==expectedRead", result)
        self.assertIn("(ramEnable)==(cpuEnable && address<16'h8000)", result)
        self.assertIn("expectedSelect==0 ? (ramResult)", result)
        self.assertIn("romSelect==expectedBank", result)

    def test_detached_clocks_missing_ports_and_changed_state_are_rejected(self):
        for broken in (
            MEMORY_PARENT.replace(".clk(clk)", ".clk(other)"),
            MEMORY_PARENT.replace(".reset(reset)", ".reset(other)"),
            MEMORY_PARENT.replace(".enable(cpuEnable)", ".enable(other)"),
            MEMORY_PARENT.replace(".read_data(cpuRead)", ".unused(cpuRead)"),
            MEMORY_PARENT.replace(".io_cpuReadData(ramResult)", ".unused(ramResult)"),
            MEMORY_PARENT.replace("reg [1:0] memorySelect;", "reg [2:0] memorySelect;"),
            MEMORY_PARENT.replace("reg romPresent;", "reg romPresent; reg romPresent;"),
        ):
            with self.subTest(broken=broken), self.assertRaises(ValueError):
                observed(broken, "Machine")


if __name__ == "__main__":
    unittest.main()
