"""Check source-derived C scalar retirement against the existing ISA contract.

The frontend is an explicitly recorded trust boundary. A proof of its
translation correctness, complete bus histories and machine composition
are separate obligations.
"""

import json
import shutil
import subprocess
from pathlib import Path

from pycparser import c_ast, c_parser

from tools.bbc import cpu_instruction_proof, cycle_reference
from tools.bbc.proofs.cpu_reference import frontend
from tools.bbc.proofs.video_cursor import hashes, replace_once
from tools.validation.common import ROOT, digest, write_json


def qualify():
    directory = ROOT / "build/bbc/cpu-c-scalar-architecture-bridge"
    directory.mkdir(exist_ok=True)
    native = ROOT / "build/bbc/cpu-c-reference-transpiler/native-comparison-result.json"
    native_report = json.loads(native.read_text())
    if not native_report.get("passed") or not native_report.get("inputs_unchanged"):
        raise ValueError("Current independently compiled C adapter comparison required")
    for group in ("inputs", "artifacts"):
        if any(digest(ROOT / name) != checksum for name, checksum in native_report[group].items()):
            raise ValueError("C adapter comparison seal changed: " + group)
    source = ROOT / "build/bbc/reference-cycle/m6502.h"
    expanded = ROOT / "build/bbc/cpu-c-reference-transpiler/expanded.c"
    original = source.with_name("m6502-original.h")
    if source.read_text() != cycle_reference.derive(original.read_text()):
        raise ValueError("Changed maintained C reference derivation")
    opcodes = sorted(
        set(cpu_instruction_proof.SCALAR_OPCODES)
        - {0, 0x10, 0x30, 0x50, 0x70, 0x90, 0xB0, 0xD0, 0xF0}
        | {0x24}
    )
    microcodes = {(op << 3) | (2 if op == 0x24 else 1) for op in opcodes}
    ast = c_parser.CParser().parse(expanded.read_text())
    switches = []

    class FindSwitch(c_ast.NodeVisitor):
        def visit_Switch(self, node):
            switches.append(node)

    FindSwitch().visit(ast)
    if len(switches) != 1:
        raise ValueError("Changed C decoder inventory")
    original_cases = switches[0].stmt.block_items
    evaluator = frontend.Frontend(ast)
    selected = [
        case
        for case in original_cases
        if isinstance(case, c_ast.Case) and evaluator.case_value(case.expr) in microcodes
    ]
    if len(selected) != len(microcodes):
        raise ValueError("Missing scalar C microcode")
    # Only whole case alternatives are omitted. Function bodies, dispatch
    # expression, common entry/exit and selected case ASTs are unchanged.
    switches[0].stmt.block_items = selected + [
        case for case in original_cases if isinstance(case, c_ast.Default)
    ]
    translated = directory / "scalar.v"
    translated.write_text(frontend.Frontend(ast).generate())
    contract = ROOT / "sim/formal/BbcCpuArchitectureContract.v"
    files = [
        Path(__file__),
        Path(frontend.__file__),
        Path(cpu_instruction_proof.__file__),
        Path(cycle_reference.__file__),
        cycle_reference.SOURCE / "lock.json",
        native,
        source,
        original,
        expanded,
        contract,
        Path(shutil.which("yosys")),
    ]
    before = hashes(files)
    offsets = {}
    position = 256
    for name, width in frontend.FIELDS:
        position -= width
        offsets[name] = (position, width)

    def field(name, signal="nextState"):
        low, width = offsets[name]
        return f"{signal}[{low + width - 1}:{low}]"

    values = {"IR": "{5'b0,opcode,(opcode==8'h24 ? 3'd2 : 3'd1)}", "bcd_enabled": "8'd1"}
    pack = ",".join(values.get(name, field(name, "seed")) for name, _ in frontend.FIELDS)
    harness = directory / "contract.v"
    harness.write_text(
        """module CArchitectureBridge(input [7:0] opcode,operand,input [255:0] seed,output equivalent);
wire [255:0] initialState={"""
        + pack
        + """};
wire [255:0] nextState;
wire [63:0] pins;
wire trap;
M6502ReferenceNext reference(.state_in(initialState),.pins_in({40'b0,operand,16'b0}|64'h1000000),
 .state_out(nextState),.pins_out(pins),.trap(trap));
wire [7:0] expectedA,expectedX,expectedY,expectedS,expectedP,memoryResult;
BbcCpuArchitectureContract architecture("""
        + ",".join(
            f".{port}({field(name, 'initialState')})"
            for port, name in (("a", "A"), ("x", "X"), ("y", "Y"), ("s", "S"), ("p", "P"))
        )
        + """,
 .opcode(opcode),.operand(operand),.isRmw(1'b0),.isIncDec(1'b0),
 .expectedA(expectedA),.expectedX(expectedX),.expectedY(expectedY),
 .expectedS(expectedS),.expectedP(expectedP),.memoryResult(memoryResult));
wire admitted="""
        + " || ".join(f"opcode==8'h{op:02x}" for op in opcodes)
        + ";\n"
        + "assign equivalent=!admitted || (!trap && "
        + " && ".join(
            f"{field(name)}=={expected}"
            for name, expected in (
                ("A", "expectedA"),
                ("X", "expectedX"),
                ("Y", "expectedY"),
                ("S", "expectedS"),
                ("P", "expectedP"),
            )
        )
        + f" && {field('PC')}=={field('PC', 'initialState')} && pins[15:0]=={field('PC', 'initialState')} && pins[25:24]==2'b11);\nendmodule\n"
    )
    # An actual C semantic mutation is regenerated from a separate source,
    # while the architectural contract and unchanged native source stay fixed.
    wrong_source = directory / "wrong-clear-carry.h"
    wrong_source.write_text(
        replace_once(
            source.read_text(),
            "case (0x18<<3)|1: c->P&=~0x1;_FETCH();break;",
            "case (0x18<<3)|1: c->P|=0x1;_FETCH();break;",
        )
    )
    wrong_expanded = subprocess.check_output(
        ["gcc", "-E", "-P", "-"], input=frontend.extract(wrong_source), text=True
    )
    wrong_ast = c_parser.CParser().parse(wrong_expanded)
    switches = []
    FindSwitch().visit(wrong_ast)
    evaluator = frontend.Frontend(wrong_ast)
    switches[0].stmt.block_items = [
        case
        for case in switches[0].stmt.block_items
        if isinstance(case, c_ast.Default)
        or isinstance(case, c_ast.Case)
        and evaluator.case_value(case.expr) in microcodes
    ]
    variants = [
        ("positive", translated.read_text()),
        ("wrong-clear-carry", frontend.Frontend(wrong_ast).generate()),
    ]
    results = []
    for name, text in variants:
        rtl = directory / (name + ".v")
        rtl.write_text(text)
        witness = directory / (name + "-counterexample.json")
        witness.unlink(missing_ok=True)
        command = f"read_verilog -formal -sv {rtl} {contract} {harness}; prep -top CArchitectureBridge -flatten; check -assert; opt; write_json {directory}/{name}.json; sat -seq 1 -prove equivalent 1 -verify -timeout 120 -show equivalent -show opcode -dump_json {witness}"
        log = directory / (name + ".log")
        with log.open("w") as out:
            process = subprocess.run(
                ["yosys", "-Q", "-T", "-p", command],
                stdout=out,
                stderr=subprocess.STDOUT,
                timeout=240,
            )
        content = log.read_text()
        accepted = (
            process.returncode == 0 and "SAT proof finished - no model found: SUCCESS!" in content
            if name == "positive"
            else process.returncode != 0
            and "ERROR: Called with -verify and proof did fail!" in content
            and witness.is_file()
            and any(
                s["name"] == "equivalent" and "0" in s.get("wave", "")
                for s in json.loads(witness.read_text())["signal"]
            )
        )
        results.append({"name": name, "accepted": accepted})
        if not accepted:
            raise ValueError("C scalar architecture bridge failed: " + name)
    stable = before == hashes(files)
    report = {
        "passed": stable,
        "inputs": before,
        "inputs_unchanged": stable,
        "opcodes": opcodes,
        "proofs": results,
        "artifacts": hashes(
            [p for p in directory.iterdir() if p.is_file() and p.name != "result.json"]
        ),
        "scope": "Source-derived maintained C scalar final microcycles agree with the same existing architectural equations used in actual FPGA proofs, for all input registers/flags/operand bytes, PC values and unused reference state. Full 8-bit flags and A/X/Y/S, unchanged PC and returned fetch address/RW/SYNC are checked. Exact whole-case specialization retains source entry/exit/helper ASTs. An actual C CLC semantic mutation must fail.",
        "limits": "The custom C frontend is a recorded trust boundary, checked with finite independently compiled C transitions rather than proved correct for all C inputs. Local final-microcycle theorem, not a proof of complete instruction/bus/interrupt histories or arbitrary reference reachability. Source includes the existing documented branch-IRQ/NMI corrections. No universal CPU/game equivalence.",
        "universal_game_equivalence": False,
    }
    write_json(directory / "result.json", report)
    if not stable:
        raise ValueError("C architecture inputs changed")
    return report


if __name__ == "__main__":
    qualify()
