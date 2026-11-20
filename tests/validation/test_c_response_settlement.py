"""Keep response timing proofs conservative and bound to actual capture guards."""

import copy
import unittest

from tools.bbc.proofs.cpu_reference import response_settlement

SOURCE = """module Parent (input wire clk,input wire reset);
  BbcCpu cpu (
    .clk(clk),.reset(reset),.enable(cpuEnable),.read_data(cpuRead)
  );
  BbcRam ram (
    .clk(clk),.reset(reset)
  );
  assign tickCpu = (clock32 && (phase_1[3 : 0] == 4'b1111));
  always @(posedge clk) begin
    if(reset)begin delayedSlow <= 1'b0;end
    else begin
      if(tickCpu) begin
        delayedSlow <= (decode_io_slow && (! cpuEnable));
      end
    end
  end
  always @(posedge clk) begin
    if(cpuEnable) begin
      rom_spinal_port0 <= rom[romAddress];
    end
  end
endmodule
module BbcRam (
input wire clk,input wire reset
);
always @(posedge clk)begin
 if(io_cpuEnable_regNext)begin response<=data;end
end
endmodule
"""


class ResponseSettlementTests(unittest.TestCase):
    def test_only_raw_rom_byte_is_abstracted_and_ram_capture_is_preserved(self):
        observed, removed = response_settlement.observed(SOURCE, "Parent", 1)
        self.assertEqual(removed, ["cpu"])
        self.assertIn("if(cpuEnable) begin\n      rom_spinal_port0 <= proofRomByte;", observed)
        self.assertIn("if(io_cpuEnable_regNext)begin response<=data;end", observed)
        self.assertIn("wire [7:0] proofRead=cpuRead;", observed)

    def test_changed_clock_binding_and_duplicate_rom_read_are_rejected(self):
        for changed in (
            SOURCE.replace(".enable(cpuEnable)", ".enable(tickCpu)"),
            SOURCE.replace(".reset(reset),.enable", ".reset(otherReset),.enable"),
            SOURCE.replace("if(tickCpu) begin", "if(clock32) begin"),
            SOURCE.replace(
                "rom_spinal_port0 <= rom[romAddress];",
                "rom_spinal_port0 <= rom[romAddress];\nrom_spinal_port0 <= rom[romAddress];",
            ),
        ):
            with self.subTest(source=changed), self.assertRaises(ValueError):
                response_settlement.observed(changed, "Parent", 1)

    def test_unsupported_clocks_are_rejected_before_source_access(self):
        for multiplier in (0, 2, 4):
            with (
                self.subTest(multiplier=multiplier),
                self.assertRaisesRegex(ValueError, "master clock"),
            ):
                response_settlement.observed("", "Parent", multiplier)

    def graph(self):
        module = {
            "ports": {"reset": {"bits": [2]}},
            "netnames": {},
            "cells": {
                "initial": {"type": "$initstate", "connections": {"Y": [3]}},
                "initial_reset": {"type": "$assume", "connections": {"A": [2], "EN": [3]}},
            },
        }
        first = 10
        for name, width in (
            ("cpu_address", 16),
            ("cpu_write_enable", 1),
            ("cpu_write_data", 8),
            ("peripheral", 8),
            ("proofRomByte", 8),
            ("proofRamByte", 8),
        ):
            bits = list(range(first, first + width))
            first += width
            module["netnames"][name] = {"bits": bits, "attributes": {}}
            module["cells"][name] = {"type": "$anyseq", "connections": {"Y": bits}}
        return {"modules": {"Parent": module}}

    def test_assumptions_constants_aliases_and_initialized_inputs_are_rejected(self):
        graph = self.graph()
        response_settlement.guard_domain(graph, "Parent")
        for fault in ("assumption", "continuous-reset", "constant", "aliased", "initialized"):
            changed = copy.deepcopy(graph)
            module = changed["modules"]["Parent"]
            if fault == "assumption":
                module["cells"]["extra"] = {"type": "$assume", "connections": {}}
            elif fault == "continuous-reset":
                module["cells"]["initial_reset"]["connections"]["EN"] = ["1"]
            elif fault == "constant":
                module["netnames"]["cpu_address"]["bits"] = ["0"] * 16
            elif fault == "aliased":
                module["netnames"]["proofRamByte"]["bits"] = module["netnames"]["proofRomByte"][
                    "bits"
                ]
            else:
                module["netnames"]["proofRomByte"]["attributes"]["init"] = "0" * 8
            with self.subTest(fault=fault), self.assertRaises(ValueError):
                response_settlement.guard_domain(changed, "Parent")


if __name__ == "__main__":
    unittest.main()
