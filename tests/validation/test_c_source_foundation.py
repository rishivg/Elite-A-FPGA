"""Check exhaustive partition admission and preservation certificates."""

import re
import unittest

from tools.bbc import cycle_reference
from tools.bbc.proofs.cpu_reference import foundation, prune, pruning_certificate


class PruningCertificateTests(unittest.TestCase):
    SOURCE = """module M6502ReferenceNext(input [255:0] state_in,input [63:0] pins_in,output [255:0] state_out,output [63:0] pins_out,output trap);
wire [255:0] ssa_state;
assign ssa_state=state_in;
wire [7:0] ssa_unused;
assign ssa_unused=state_in[7:0];
wire [63:0] ssa_pins;
assign ssa_pins=pins_in;
wire [0:0] ssa_trap;
assign ssa_trap=1'b0;
assign state_out=ssa_state;
assign pins_out=ssa_pins;
assign trap=ssa_trap;
endmodule
"""

    def test_only_unreachable_equations_can_be_removed(self):
        reduced, _ = prune.prune(self.SOURCE)
        certificate = pruning_certificate.verify(self.SOURCE, reduced)
        self.assertEqual(certificate["retained_definitions"], 3)
        for changed in (
            reduced.replace("assign ssa_state=state_in;", "assign ssa_state=~state_in;"),
            reduced.replace("wire [63:0] ssa_pins;", "wire signed [63:0] ssa_pins;"),
            reduced.replace("wire [63:0] ssa_pins;\nassign ssa_pins=pins_in;\n", ""),
            reduced.replace("assign trap=ssa_trap;", ""),
        ):
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                pruning_certificate.verify(self.SOURCE, changed)

    def test_cycles_and_unknown_inputs_are_rejected(self):
        for expression in ("ssa_state", "unregistered_input"):
            changed = self.SOURCE.replace(
                "assign ssa_state=state_in;", f"assign ssa_state={expression};"
            )
            with self.subTest(expression=expression), self.assertRaises(ValueError):
                pruning_certificate.verify(changed, changed)


class CompleteSourcePartitionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = (cycle_reference.DIRECTORY / "m6502.h").read_text()

    def test_all_defined_microsteps_and_fetch_bytes_are_covered(self):
        cover = foundation.verify_partition_cover(self.source)
        self.assertEqual(cover["defined_microsteps"], 1115)
        self.assertEqual(cover["sync_bytes"], 256)
        self.assertEqual(cover["partitions"], 65)

    def test_selected_case_bodies_are_verbatim_and_default_is_guarded(self):
        pattern = r"case \(0x([0-9A-Fa-f]{2})<<3\)\|([0-7]): ([^\n]+)"
        original = {
            (int(opcode, 16), int(step)): body
            for opcode, step, body in re.findall(pattern, self.source)
        }
        self.assertEqual(len(original), 2048)
        for kind, index in foundation.PARTITIONS:
            checked = foundation.checked_header(self.source, kind, index)
            retained = {
                (int(opcode, 16), int(step)): body
                for opcode, step, body in re.findall(pattern, checked)
            }
            expected = {
                key: value
                for key, value in original.items()
                if (kind == "execute" and key[0] // 8 == index)
                or (kind == "sync" and key[1] == 0 and (key[0] == 0 or key[0] // 8 == index))
            }
            self.assertEqual(retained, expected, (kind, index))
            self.assertEqual(checked.count("default: assert(false);break;"), 1)

    def test_changed_case_inventory_and_fallthrough_are_rejected(self):
        for changed in (
            self.source.replace("case (0x00<<3)|0:", "case (0x00<<3)|8:"),
            self.source.replace(
                "case (0x02<<3)|0: _SA(c->PC);break;", "case (0x02<<3)|0: _SA(c->PC);"
            ),
        ):
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                foundation.checked_header(changed, "execute", 0)

    def test_unknown_partition_is_rejected(self):
        for kind, index in (("hold", 1), ("execute", 32), ("sync", -1), ("other", 0)):
            for builder in (foundation.checked_header, foundation.partition_harness):
                with (
                    self.subTest(kind=kind, index=index, builder=builder),
                    self.assertRaises(ValueError),
                ):
                    builder(self.source, kind, index)


if __name__ == "__main__":
    unittest.main()
