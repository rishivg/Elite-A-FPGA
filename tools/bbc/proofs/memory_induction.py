"""Prove short/branch streams mixed with independently inventoried memory instructions."""

import argparse
import re
from pathlib import Path

from tools.bbc import cpu_instruction_proof
from tools.bbc.proofs import branch_induction
from tools.validation.common import ROOT

ADDRESS_CLASSES = {
    "zeroPage": 1,
    "zeroX": 2,
    "zeroY": 3,
    "absolute": 4,
    "absoluteX": 5,
    "absoluteY": 6,
    "indirectX": 7,
    "indirectY": 8,
}


def qualify(all_memory=False):
    """Check every declared addressing/semantic fact against the independent opcode inventory."""
    formal = ROOT / "sim/formal"
    inventory = ROOT / "tools/bbc/reference/official_stimulus.h"
    bounded = formal / "BbcCpuMemoryContract.v"
    cpu_instruction_proof.memory_groups(bounded.read_text(), inventory.read_text())
    contract = "BbcCpuMemoryStreamContract" if all_memory else "BbcCpuAbsoluteStreamContract"
    source = (formal / (contract + ".v")).read_text()
    records = re.findall(
        r" 8'h([0-9a-f]{2}):begin mode=(\d+);semanticOpcode=8'h([0-9a-f]{2});"
        r"isRmw=([01]);isStore=([01]);isIncDec=([01]);end",
        bounded.read_text(),
    )
    expected = {
        int(op, 16): (int(mode), int(semantic, 16), bool(int(rmw)))
        for op, mode, semantic, rmw, _, _ in records
        if all_memory or int(mode) in (1, 4) and not int(rmw)
    }
    actual = {}
    semantics = {
        int(op, 16): int(semantic, 16)
        for op, semantic in re.findall(r"8'h([0-9a-f]{2}):semantic=8'h([0-9a-f]{2});", source)
    }
    rmw_function = re.search(r"function rmw;(.*?)endfunction", source, re.S)
    rmw_ops = (
        set(int(op, 16) for op in re.findall(r"op==8'h([0-9a-f]{2})", rmw_function[1]))
        if rmw_function
        else set()
    )
    for name, mode in ADDRESS_CLASSES.items():
        function = re.search(r"function " + name + r";(.*?)endfunction", source, re.S)
        if function is None:
            continue
        for value in re.findall(r"op==8'h([0-9a-f]{2})", function[1]):
            opcode = int(value, 16)
            if opcode in actual:
                raise ValueError("Duplicate addressing class")
            actual[opcode] = (mode, semantics[opcode], opcode in rmw_ops)
    if actual != expected or rmw_ops != {op for op, facts in expected.items() if facts[2]}:
        raise ValueError("Stream memory facts differ from independent documented opcode inventory")
    opcodes = sorted({op for op in cpu_instruction_proof.SCALAR_OPCODES if op != 0} | set(expected))
    faults = [("omitted-store", "ZP0:     WE = store;", "ZP0:     WE = 0;")]
    if all_memory:
        faults.append(("omitted-rmw-write", "WRITE:   WE = 1;", "WRITE:   WE = state != WRITE;"))
    return branch_induction.qualify(
        contract=contract,
        opcodes=opcodes,
        name="cpu-memory-induction-proof" if all_memory else "cpu-direct-memory-induction-proof",
        initial_steps=64 if all_memory else 52,
        max_steps=80 if all_memory else 64,
        extra_faults=faults,
        extra_inputs=[Path(__file__), inventory, bounded],
        scope_extension=(
            "All 100 documented memory opcodes, all eight addressing modes, indexed and pointer wrap, "
            "page-crossing dummy reads, store data and both RMW writes are independently modeled. "
            "Architectural state is sampled after each retirement DECODE; intermediate execution flags "
            "are not retirement state. Actual omitted store and final RMW write controls must fail base cases."
            if all_memory
            else "All 30 documented zero-page and absolute read/store opcodes are independently modeled. "
            "Written bytes and exact write timing remain checked. The actual omitted-store control must fail a base case."
        ),
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--all-memory", action="store_true")
    qualify(all_memory=parser.parse_args().all_memory)
