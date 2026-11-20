# Arlet's NMOS 6502

Source: https://github.com/Arlet/verilog-6502
Pinned commit: `e930327ffecc5062bfce70bbcfba2bbfa6de6e4c`.
`cpu.v` and `ALU.v` are retained unchanged. The permission and copyright notice
at the top of `cpu.v` applies to the core; retain it in redistributions.

The core requires synchronous read memory: data for a presented address is
available on the following enabled CPU cycle. `tools/bbc/prepare.py` verifies
both upstream hashes and generates `build/bbc/ArletCpuEnabled.v`, retaining the
notice. The derived core adds a true clock enable (RDY changes its address
mux) and preserves an NMI arriving after an IRQ vector has been selected.
The latter is checked by sweeping 16 interrupt-edge placements; the unchanged
upstream behavior loses one of those NMIs. Original upstream files remain
available for review, and the generated derivative has its own recorded hash.

The derivative also polls IRQ on the NMOS instruction cycles, preserving the
old I flag for CLI/SEI/PLP, the special taken-branch polling rules, and the B
flag of a hardware interrupt whose request has already fallen. Independent
fixtures exercise arrival times and short pulses through those instructions.
