"""Guard the actual state and parent wiring consumed by bus-device proofs."""

import unittest

from tools.bbc.bus_binding_proof import module, observed
from tools.bbc.serial_idle_proof import observed_module

SERIAL = """module BbcSerial(input clk, reset);
reg [7:0] control;
reg [2:0] baud;
reg rs423, empty, pending;
reg [18:0] remaining, transmitPeriod;
always @(posedge clk) begin
control <= 0; baud <= 0; rs423 <= 0; empty <= 0; pending <= 0;
remaining <= 0; transmitPeriod <= 0;
end
endmodule
""".replace("reg rs423, empty, pending;", "reg rs423; reg empty; reg pending;").replace(
    "reg [18:0] remaining, transmitPeriod;", "reg [18:0] remaining; reg [18:0] transmitPeriod;"
)

PARENT = """module Machine(input clk, reset);
BbcSerial serial (
.clk(clk), .reset(reset), .io_tick(tickCpu), .io_address(serialAddress),
.io_data(cpuData), .io_aciaRead(aciaRead), .io_aciaWrite(aciaWrite),
.io_serialRead(serialRead), .io_serialWrite(serialWrite)
);
BbcAddressDecode decode (
.io_address(cpu_address[15:0])
);
BbcCpu cpu (
.irq(cpuIrq)
);
Intel8271 fdc (
.io_result(fdcData[7:0])
);
assign when_BbcMachine_l245 = (write && decode_io_romSelect);
endmodule
"""


class BusDeviceProofTests(unittest.TestCase):
    def test_observation_preserves_logic_and_rejects_missing_or_added_state(self):
        result = observed_module(SERIAL)
        self.assertIn("remaining <= 0; transmitPeriod <= 0;", result)
        self.assertIn("output wire [51:0] proofState", result)
        for broken in (
            SERIAL.replace("reg pending;", "reg other;"),
            SERIAL.replace("pending <= 0;", "other <= 0;"),
            SERIAL.replace("control <= 0;", "control <= 0; extraState <= 1;"),
        ):
            with self.subTest(broken=broken), self.assertRaises(ValueError):
                observed_module(broken)

    def test_missing_duplicate_or_wrong_actual_parent_bindings_fail(self):
        instrumented, connections = observed(PARENT, "Machine")
        self.assertEqual(connections["proof_fdc_result"], "fdcData")
        self.assertIn("serialAddress == cpu_address[0]", instrumented)
        self.assertIn("cpuData == cpu_write_data", instrumented)
        for broken in (
            PARENT + PARENT,
            PARENT.replace(".clk(clk)", ".clk(other)"),
            PARENT.replace(".reset(reset)", ".reset(other)"),
            PARENT.replace(".io_tick(tickCpu)", ".io_tick(tickVia)"),
            PARENT.replace("cpu_address[15:0]", "other[15:0]"),
            PARENT.replace("BbcSerial serial", "BbcSerial elsewhere"),
            PARENT.replace("write && decode_io_romSelect", "write"),
        ):
            with self.subTest(broken=broken), self.assertRaises(ValueError):
                observed(broken, "Machine")
        with self.assertRaises(ValueError):
            module(PARENT, "Other")


if __name__ == "__main__":
    unittest.main()
