"""Prove all documented instruction input domains after a normal CPU prefix."""

import json
import re
import shutil
import subprocess
from pathlib import Path

from tools.validation.common import ROOT, digest, write_json

from . import build, prepare

DIRECTORY = build.BBC / "cpu-instruction-proof"
SCALAR_OPCODES = (
    0x00,
    0xEA,
    0x18,
    0x38,
    0xD8,
    0xF8,
    0xB8,
    0x58,
    0x78,
    0xCA,
    0x88,
    0xE8,
    0xC8,
    0x8A,
    0x98,
    0xAA,
    0xA8,
    0x9A,
    0xBA,
    0x0A,
    0x4A,
    0x2A,
    0x6A,
    0x09,
    0x29,
    0x49,
    0x69,
    0xE9,
    0xC9,
    0xE0,
    0xC0,
    0xA9,
    0xA2,
    0xA0,
    0x10,
    0x30,
    0x50,
    0x70,
    0x90,
    0xB0,
    0xD0,
    0xF0,
)
CONTROL_OPCODES = (0x08, 0x48, 0x28, 0x68, 0x4C, 0x6C, 0x20, 0x60, 0x40)
MODES = {
    "ZeroPage": 1,
    "ZeroPageX": 2,
    "ZeroPageY": 3,
    "Absolute": 4,
    "AbsoluteX": 5,
    "AbsoluteY": 6,
    "IndexedIndirect": 7,
    "IndirectIndexed": 8,
}
SEMANTICS = {
    "ORA": 0x09,
    "AND": 0x29,
    "EOR": 0x49,
    "ADC": 0x69,
    "SBC": 0xE9,
    "CMP": 0xC9,
    "CPX": 0xE0,
    "CPY": 0xC0,
    "LDA": 0xA9,
    "LDX": 0xA2,
    "LDY": 0xA0,
    "BIT": 0x24,
    "ASL": 0x0A,
    "LSR": 0x4A,
    "ROL": 0x2A,
    "ROR": 0x6A,
    "INC": 0xE8,
    "DEC": 0xCA,
    "STA": 0x81,
    "STX": 0x86,
    "STY": 0x84,
}


FAULTS = (
    ("old-brk-padding", "adc_sbc <= state != BRK0;", "adc_sbc <= 1;", 0),
    ("wrong-clear-carry", "if( clc ) C <= 0;", "if( clc ) C <= 1;", 0),
    (
        "old-decimal-flag",
        "adc_bcd <= (plp ? ADD[3] : cld ? 1'b0 : sed ? 1'b1 : D) && state != BRK0;",
        "adc_bcd <= D;",
        0,
    ),
    ("wrong-branch-direction", "backwards <= DIMUX[7];", "backwards <= !DIMUX[7];", 0),
    (
        "wrong-indirect-pointer-increment",
        "JMPI1:          PC_temp = { DIMUX, ADD + 8'h01 };",
        "JMPI1:          PC_temp = { DIMUX, ADD };",
        1,
    ),
    ("wrong-rmw-write-byte", "WRITE:   DO = ADD;", "WRITE:   DO = DIMUX;", 2),
)


def supporting_evidence(path):
    """Seal complete independently executed CPU comparisons, including producers."""
    report = json.loads(path.read_text())
    if report.get("passed") is not True or report.get("inputs_unchanged") is not True:
        raise ValueError("Incomplete independent CPU evidence")
    periods = report.get("periods", [])
    if [p.get("period") for p in periods] != [1, 16, 48] or any(
        p.get("passed") is not True for p in periods
    ):
        raise ValueError("Missing complete independent CPU clock comparisons")
    for group in ("inputs", "artifacts"):
        hashes = report.get(group, {})
        if group == "inputs" and "sources" in hashes:
            hashes = hashes["sources"]
        if not hashes or any(digest(ROOT / name) != sha for name, sha in hashes.items()):
            raise ValueError("Changed independent CPU evidence: " + group)
    return report


def mutated(core, correct, wrong):
    if core.count(correct) != 1:
        raise ValueError("Changed actual CPU mutation site: " + correct)
    return core.replace(correct, wrong)


def memory_groups(contract, inventory):
    """Check declared opcode facts, then specialize only the expected model."""
    original = re.findall(r"\{0x([0-9a-f]{2}), (\w+)\},\s*// (\w+)", inventory)
    expected = {}
    for opcode, mode, name in original:
        if mode in MODES:
            expected[int(opcode, 16)] = (
                MODES[mode],
                SEMANTICS[name],
                int(name in {"ASL", "LSR", "ROL", "ROR", "INC", "DEC"}),
                int(name.startswith("ST")),
                int(name in {"INC", "DEC"}),
            )
    if (
        len(original) != 151
        or len(expected) != 100
        or {int(op, 16) for op, _, _ in original}
        != set(expected) | set(SCALAR_OPCODES) | set(CONTROL_OPCODES)
    ):
        raise ValueError("Incomplete documented opcode inventory")
    pattern = re.compile(
        r" 8'h([0-9a-f]{2}):begin mode=(\d+);semanticOpcode=8'h([0-9a-f]{2});"
        r"isRmw=([01]);isStore=([01]);isIncDec=([01]);end"
    )
    records = pattern.findall(contract)
    actual = {
        int(op, 16): (int(mode), int(semantic, 16), int(rmw), int(store), int(inc))
        for op, mode, semantic, rmw, store, inc in records
    }
    if len(records) != 100 or actual != expected:
        raise ValueError("Changed memory opcode/addressing/operation facts")
    groups = []
    for mode in range(1, 9):
        text = contract.replace("reg [3:0] mode;", f"wire [3:0] mode=4'd{mode};").replace(
            " mode=0;semanticOpcode=0;", " semanticOpcode=0;"
        )
        lines, opcodes = [], []
        for line in text.splitlines():
            match = pattern.fullmatch(line)
            if match:
                if int(match[2]) != mode:
                    continue
                opcodes.append(int(match[1], 16))
                line = line.replace(f"mode={mode};", "", 1)
            lines.append(line)
        if not opcodes or f"wire [3:0] mode=4'd{mode};" not in text:
            raise ValueError("Missing constant addressing-mode specialization")
        groups.append((mode, opcodes, "\n".join(lines) + "\n"))
    return groups


def guard_assumptions(graph, top):
    """Reject extra input restrictions that could make exhaustive claims vacuous."""
    module = graph["modules"][top]
    opcode = set(module["netnames"]["opcode"]["bits"])
    assumptions = [c for c in module["cells"].values() if c["type"] == "$assume"]
    if len(opcode) != 8 or len(assumptions) != 1 or assumptions[0]["connections"]["EN"] != ["1"]:
        raise ValueError("Expected one unconditional opcode-only domain assumption")
    drivers = {}
    for cell in module["cells"].values():
        for port, direction in cell["port_directions"].items():
            if direction == "output":
                for bit in cell["connections"][port]:
                    if isinstance(bit, int):
                        if bit in drivers:
                            raise ValueError("Multiple formal signal drivers")
                        drivers[bit] = cell
    pending, seen, leaves = list(assumptions[0]["connections"]["A"]), set(), set()
    while pending:
        bit = pending.pop()
        if bit in {"0", "1"} or bit in seen:
            continue
        seen.add(bit)
        if bit in opcode:
            if drivers[bit]["type"] != "$anyconst":
                raise ValueError("Opcode is not a free symbolic constant")
            leaves.add(bit)
            continue
        cell = drivers.get(bit)
        if cell is None or any(t in cell["type"].lower() for t in ("ff", "mem", "any", "latch")):
            raise ValueError("Domain assumption restricts state or another input")
        for port, direction in cell["port_directions"].items():
            if direction == "input":
                pending.extend(cell["connections"][port])
    if leaves != opcode:
        raise ValueError("Incomplete symbolic opcode domain")


def cache_current(saved, inputs, tools, opcodes):
    """Reuse only complete, unchanged proofs with explicit bounded scope."""
    try:
        proofs = saved["proofs"]
        artifacts = saved["artifacts"]
        names = ["proof", "control-proof", *[f"memory-mode{mode}" for mode in range(1, 9)]]
        return (
            saved.get("schema") == 1
            and saved.get("passed") is True
            and saved.get("inputs_unchanged") is True
            and saved.get("opcode_coverage_complete") is True
            and saved.get("universal_cpu_equivalence") is False
            and saved.get("universal_game_equivalence") is False
            and saved.get("inputs") == inputs
            and saved.get("tools") == tools
            and saved.get("opcodes") == opcodes
            and [p["name"] for p in proofs] == names
            and all(p["passed"] is True and p["nonempty_domains"] is True for p in proofs)
            and sorted(op for p in proofs for op in p["opcodes"]) == opcodes
            and len(saved["negative_controls"]) == len(FAULTS)
            and all(
                n["name"] == f[0] and n["detected"] is True
                for n, f in zip(saved["negative_controls"], FAULTS, strict=True)
            )
            and bool(artifacts)
            and all(digest(ROOT / name) == sha for name, sha in artifacts.items())
        )
    except (KeyError, OSError, TypeError, ValueError):
        return False


def qualify():
    prepare.main()
    support = [
        build.BBC / name / "result.json"
        for name in (
            "cpu-brk",
            "cpu-sequences",
            "cpu-arithmetic",
            "cpu-decimal-transitions",
        )
    ]
    evidence = [supporting_evidence(path) for path in support]
    core = build.BBC / "ArletCpuEnabled.v"
    wrapper = ROOT / "rtl/bbc/BbcCpu.v"
    alu = ROOT / "third_party/6502/ALU.v"
    formal = ROOT / "sim/formal"
    scalar = formal / "BbcCpuInstructionContract.v"
    control = formal / "BbcCpuControlContract.v"
    memory = formal / "BbcCpuMemoryContract.v"
    common = [formal / name for name in ("BbcCpuContractPrefix.v", "BbcCpuArchitectureContract.v")]
    inventory = ROOT / "tools/bbc/reference/official_stimulus.h"
    groups = memory_groups(memory.read_text(), inventory.read_text())
    dependencies = [
        Path(__file__),
        Path(prepare.__file__),
        core,
        wrapper,
        alu,
        scalar,
        control,
        memory,
        *common,
        inventory,
        *support,
    ]
    before = build.hashes(dependencies)
    tools = {
        "yosys": subprocess.check_output(["yosys", "-V"], text=True).strip(),
        "yosys_sha256": digest(Path(shutil.which("yosys"))),
    }
    DIRECTORY.mkdir(exist_ok=True)
    expected_opcodes = sorted(
        f"{op:02x}"
        for op in (*SCALAR_OPCODES, *CONTROL_OPCODES, *[op for _, ops, _ in groups for op in ops])
    )
    saved = build.load_receipt("cpu-instruction-proof/result.json")
    if cache_current(saved, before, tools, expected_opcodes):
        print(
            "Verified unchanged all-151 instruction proof, admitted domains, six faults and sealed independent evidence",
            flush=True,
        )
        return saved

    def prove(text, name, top, contract):
        model = DIRECTORY / (name + ".v")
        model.write_text(text)
        log, graph = DIRECTORY / (name + ".log"), DIRECTORY / (name + ".json")
        command = (
            f"read_verilog -formal {model} {wrapper} {alu} {contract} "
            + " ".join(map(str, common))
            + "; "
            + f"prep -top {top} -flatten; memory_map; opt; async2sync; opt_clean; write_json {graph}; "
            "sat -set-assumes -set-init-zero -seq 50 -prove equivalent 1 -verify -timeout 120"
        )
        with log.open("w") as output:
            result = subprocess.run(
                ["yosys", "-Q", "-T", "-p", command],
                cwd=ROOT,
                stdout=output,
                stderr=subprocess.STDOUT,
                timeout=150,
            )
        return result.returncode, log.read_text(), graph

    def domains(graph, top, opcodes, name):
        guard_assumptions(json.loads(graph.read_text()), top)
        # Explicitly exhibit a satisfiable initialized domain for every opcode.
        # The cone check above establishes that no other symbolic input is restricted.
        command = f"read_json {graph}; hierarchy -top {top}; " + "; ".join(
            f"sat -set-assumes -set-init-zero -seq 1 -set opcode {op} -show opcode"
            for op in opcodes
        )
        log = DIRECTORY / (name + "-domains.log")
        with log.open("w") as output:
            result = subprocess.run(
                ["yosys", "-Q", "-T", "-p", command],
                cwd=ROOT,
                stdout=output,
                stderr=subprocess.STDOUT,
                timeout=120,
            )
        text = log.read_text()
        observed = [int(value) for value in re.findall(r"\\opcode\s+(\d+)\s", text)]
        return (
            result.returncode == 0
            and text.count("SAT solving finished - model found:") == len(opcodes)
            and observed == list(opcodes)
        )

    actual = core.read_text()
    variants = [
        ("proof", "BbcCpuInstructionContract", scalar, SCALAR_OPCODES),
        ("control-proof", "BbcCpuControlContract", control, CONTROL_OPCODES),
    ]
    for mode, opcodes, text in groups:
        path = DIRECTORY / f"memory-mode{mode}-contract.v"
        path.write_text(text)
        variants.append((f"memory-mode{mode}", "BbcCpuMemoryContract", path, opcodes))
    positives = []
    for name, top, contract, opcodes in variants:
        code, log, graph = prove(actual, name, top, contract)
        positive = code == 0 and "SAT proof finished - no model found: SUCCESS!" in log
        admitted = domains(graph, top, opcodes, name)
        positives.append(
            {
                "name": name,
                "passed": positive,
                "nonempty_domains": admitted,
                "opcodes": [f"{op:02x}" for op in opcodes],
            }
        )
        print(
            f"{name}: proof={positive}, admitted_domains={admitted}, opcodes={len(opcodes)}",
            flush=True,
        )
    negatives = []
    for name, correct, wrong, variant in FAULTS:
        _, top, contract, _ = variants[variant]
        code, log, _ = prove(mutated(actual, correct, wrong), name, top, contract)
        negatives.append({"name": name, "detected": code != 0 and "proof did fail" in log})
    stable = before == build.hashes(dependencies) and evidence == [
        supporting_evidence(p) for p in support
    ]
    opcodes = [op for group in positives for op in group["opcodes"]]
    complete = len(opcodes) == len(set(opcodes)) == 151
    report = {
        "schema": 1,
        "passed": stable
        and complete
        and all(p["passed"] and p["nonempty_domains"] for p in positives)
        and all(n["detected"] for n in negatives),
        "inputs": before,
        "inputs_unchanged": stable,
        "tools": tools,
        "opcodes": sorted(opcodes),
        "opcode_coverage_complete": complete,
        "proofs": positives,
        "negative_controls": negatives,
        "physical_edges": 50,
        "enable_period": 1,
        "universal_cpu_equivalence": False,
        "universal_game_equivalence": False,
        "artifacts": build.hashes(
            [
                *DIRECTORY.glob("*.v"),
                *DIRECTORY.glob("*.log"),
                *[p for p in DIRECTORY.glob("*.json") if p.name != "result.json"],
            ]
        ),
        "scope": "Bounded exhaustive proof of actual production BbcCpu/core/ALU RTL against declared documented NMOS single-instruction bus and architectural contracts. All 151 documented opcodes, symbolic A/X/Y/S/P bytes and reset PC, all operand bytes, indirect pointers, BRK vector, relevant dummy/write-cycle read responses, page crossings and zero-page/stack/page-end wraps. Eight real reset edges and ordinary LDX/TXS/LDA/PHA/register-load/PLP prefix execute normally. Every prefix/target bus request and write byte, fetch PC and final architectural state are checked. An additional checkpoint proves that the normal prefix establishes each requested register and flag value, accounting for an immediately decoded CLI/SEI. I is sampled before the retirement DECODE edge, other state after it. One unconditional opcode-only assumption per proof and a satisfiable domain for every declared opcode are checked. Complete independently executed pin-reference regressions at periods 1/16/48 are separately sealed.",
        "limits": "One specified normal prefix, constant inactive IRQ/NMI, enable period 1, generated initial values and zero otherwise uninitialized simulation registers. Symbolic per-request bus responses need not represent one static memory image. This is not an inductive proof over arbitrary prior hidden CPU states or instruction/interrupt histories, a formal proof at periods 16/48, universal equivalence to the original CPU engine, undocumented opcode coverage, or full game equivalence.",
    }
    write_json(DIRECTORY / "result.json", report)
    if not report["passed"]:
        raise ValueError("CPU instruction proof failed; all RTL and logs retained")
    print(
        "Proved all 151 documented instruction input domains; all six faults detected", flush=True
    )
    return report


if __name__ == "__main__":
    qualify()
