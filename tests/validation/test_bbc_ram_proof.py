"""Guard RAM abstraction and binding against changed initial bytes or wiring."""

import tempfile
import unittest
from pathlib import Path

from tools.bbc.ram_proof import contract, initial_image, observed_parent

PARENT = """module BbcMachine(input clk, reset);
 reg [4:0] phase_1;
 BbcRam ram (
 .clk(clk), .reset(reset), .io_cpuEnable(cpuEnable),
 .io_videoEnable(fetch), .io_videoAddress(address[14:0]), .io_videoReadData(data[7:0])
 );
 BbcVideo video (
 .clk(clk), .reset(reset), .io_memoryRead(fetch),
 .io_memoryAddress(address[14:0]), .io_memoryData(data[7:0])
 );
endmodule
module BbcVideo(input clk, reset);
 reg [4:0] master;
endmodule
"""


class RamProofTests(unittest.TestCase):
    def test_cpu_read_observations_require_unique_actual_signals(self):
        module = """module BbcRam(input clk);
 wire [7:0] data;
 reg io_cpuEnable_regNext;
 initial $readmemb("ram.bin",memory);
endmodule
"""
        observed = contract(module, "ram.bin")
        self.assertIn("assign proofSharedData=data;", observed)
        self.assertIn("assign proofCpuReady=io_cpuEnable_regNext;", observed)
        self.assertIn("if(validCpu) assert(cpuReadData==expectedCpu);", observed)
        for signal in ("wire [7:0] data;", "reg io_cpuEnable_regNext;"):
            for broken in (module.replace(signal, ""), module.replace(signal, signal * 2)):
                with self.subTest(signal=signal), self.assertRaises(ValueError):
                    contract(broken, "ram.bin")

    def test_every_initial_word_is_checked_before_relaxing_the_array(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            image = root / "ram.bin"
            words = ["00000000"] * 256 + ["11111111"] * (32768 - 256)
            image.write_text("\n".join(words))
            module = '$readmemb("ram.bin",memory);'
            self.assertEqual(initial_image(module, root), (image, "ram.bin"))
            for index in (0, 255, 256, 19017, 32767):
                damaged = words.copy()
                damaged[index] = "11111110"
                image.write_text("\n".join(damaged))
                with self.subTest(index=index), self.assertRaises(ValueError):
                    initial_image(module, root)
            with self.assertRaises(ValueError):
                initial_image(module + module, root)
            with self.assertRaises(ValueError):
                initial_image('$readmemb("../ram.bin",memory);', root)

    def test_parent_observes_the_actual_ram_request_and_phase_relation(self):
        text, ports = observed_parent(PARENT, "BbcMachine")
        self.assertEqual(ports["io_cpuEnable"], "cpuEnable")
        self.assertIn("!(cpuEnable && fetch)", text)
        self.assertIn("phase_1==(proof_master ^ 5'h10)", text)
        self.assertIn("assign proofMaster=master;", text)
        self.assertIn("!proof_started", text)

    def test_disconnected_video_or_ram_clocks_and_missing_inputs_are_rejected(self):
        for broken in (
            PARENT.replace(".io_videoEnable(fetch)", ".io_videoEnable(other)"),
            PARENT.replace(".io_videoAddress(address[14:0])", ".io_videoAddress(other[14:0])"),
            PARENT.replace(".io_memoryData(data[7:0])", ".io_memoryData(other[7:0])"),
            PARENT.replace(".clk(clk)", ".clk(other)", 1),
            PARENT.replace(".reset(reset)", ".reset(other)", 1),
            PARENT.replace(".io_cpuEnable(cpuEnable)", ".unused(cpuEnable)"),
            PARENT.replace("reg [4:0] master;", "reg [4:0] other;"),
        ):
            with self.subTest(broken=broken), self.assertRaises(ValueError):
                observed_parent(broken, "BbcMachine")


if __name__ == "__main__":
    unittest.main()
