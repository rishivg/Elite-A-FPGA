import tempfile
import unittest
from pathlib import Path

from tools.bbc.rom_proof import initial_image, observed

PARENT = """module Machine(input clk);
reg [7:0] rom [0:49151];
reg [7:0] rom_spinal_port0;
wire [7:0] romData;
initial $readmemb("rom.bin",rom);
assign romData=rom_spinal_port0;
endmodule
"""


class RomProofGuards(unittest.TestCase):
    def test_initial_image_requires_exact_bytes_and_local_path(self):
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            (directory / "rom.bin").write_text("00000000\n11111111\n")
            image, filename = initial_image(PARENT, directory, b"\x00\xff")
            self.assertEqual(image, directory / "rom.bin")
            self.assertEqual(filename, "rom.bin")
            for parent, expected in (
                (PARENT, b"\x00\xfe"),
                (PARENT.replace('"rom.bin"', '"../rom.bin"'), b"\x00\xff"),
                (PARENT.replace('"rom.bin"', '"rom.bin",rom); $readmemb("rom.bin"'), b"\x00\xff"),
            ):
                with self.subTest(parent=parent), self.assertRaises(ValueError):
                    initial_image(parent, directory, expected)

    def test_observation_rejects_changed_bindings_and_mutable_rom(self):
        result = observed(PARENT, "Machine", "rom.bin")
        self.assertIn("assume(rom[watched]==expectedWord)", result)
        self.assertIn("if(validResponse) assert(romData==expectedWord)", result)
        self.assertNotIn("$readmemb", result)
        for broken in (
            PARENT.replace("[0:49151]", "[0:65535]"),
            PARENT.replace("rom_spinal_port0;", "other;"),
            PARENT.replace("romData=rom_spinal_port0", "romData=other"),
            PARENT.replace("endmodule", "always @(posedge clk) rom[1] <= 0;\nendmodule"),
            PARENT.replace("endmodule", "always @* rom[1] = 0;\nendmodule"),
        ):
            with self.subTest(broken=broken), self.assertRaises(ValueError):
                observed(broken, "Machine", "rom.bin")


if __name__ == "__main__":
    unittest.main()
