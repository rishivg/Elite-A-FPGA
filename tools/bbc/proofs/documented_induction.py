"""Prove unbounded streams of all 151 documented instructions from ordinary reset."""

import re
from pathlib import Path

from tools.bbc import cpu_instruction_proof
from tools.bbc.proofs import branch_induction, memory_induction
from tools.validation.common import ROOT


def qualify():
    formal = ROOT / "sim/formal"
    inventory = ROOT / "tools/bbc/reference/official_stimulus.h"
    bounded = formal / "BbcCpuMemoryContract.v"
    memory = formal / "BbcCpuMemoryStreamContract.v"
    full = formal / "BbcCpuAllStreamContract.v"
    groups = cpu_instruction_proof.memory_groups(bounded.read_text(), inventory.read_text())
    memory_source = memory.read_text()
    full_source = full.read_text()
    for name in (
        *memory_induction.ADDRESS_CLASSES,
        "semantic",
        "rmw",
        "immediate",
        "twoCycle",
        "branch",
    ):
        pattern = r"function (?:\[7:0\] )?" + name + r";.*?endfunction"
        memory_classifier = re.search(pattern, memory_source, re.S)
        full_classifier = re.search(pattern, full_source, re.S)
        if memory_classifier is None or full_classifier is None or memory_classifier[0] != full_classifier[0]:
            raise ValueError("Documented stream changed the checked classifier: " + name)
    function = re.search(r"function control;(.*?)endfunction", full_source, re.S)
    controls = set(int(op, 16) for op in re.findall(r"op==8'h([0-9a-f]{2})", function[1]))
    if controls != {0, *cpu_instruction_proof.CONTROL_OPCODES}:
        raise ValueError("Incomplete software-interrupt/stack/jump domain")
    opcodes = sorted(
        {
            *cpu_instruction_proof.SCALAR_OPCODES,
            *cpu_instruction_proof.CONTROL_OPCODES,
            *[op for _, ops, _ in groups for op in ops],
        }
    )
    if len(opcodes) != 151:
        raise ValueError("Incomplete documented instruction inventory")
    return branch_induction.qualify(
        contract="BbcCpuAllStreamContract",
        opcodes=opcodes,
        name="cpu-documented-induction-proof",
        initial_steps=68,
        max_steps=84,
        host_timeout=1800,
        extra_faults=[
            ("omitted-store", "ZP0:     WE = store;", "ZP0:     WE = 0;"),
            ("omitted-rmw-write", "WRITE:   WE = 1;", "WRITE:   WE = state != WRITE;"),
            (
                "brk-saved-b-clear",
                "BRK2:    DO = bbc_hardware_interrupt ? (P & 8'b1110_1111) : P;",
                "BRK2:    DO = P & 8'b1110_1111;",
            ),
        ],
        extra_inputs=[Path(__file__), Path(memory_induction.__file__), inventory, bounded, memory],
        scope_extension=(
            "All 151 documented opcodes compose without a fixed instruction-count limit, including all 100 memory instructions, all addressing modes, both RMW writes, stack wrap, PHP/PHA/PLP/PLA, JSR/RTS, RTI, direct/indirect JMP with NMOS page wrap and seven-cycle software BRK. Stack prefetch is retained explicitly. Architectural state is checked after every retirement DECODE; all bus requests and written values are checked continuously. Actual omitted-store, final-RMW-write and BRK-saved-B faults must each fail an initialized base case. IRQ/NMI remain inactive, warm reset remains inactive, and enable period remains one. This does not prove arbitrary interrupt histories, the parent machine, or universal Original whole-game equivalence."
        ),
    )


if __name__ == "__main__":
    qualify()
