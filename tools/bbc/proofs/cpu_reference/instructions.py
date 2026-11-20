"""Prove source-derived C instruction units against existing bus contracts.

Reference unit pre-state is abstract architectural state at an opcode fetch.
No actual FPGA state is forced. This is not a cold/interrupt/global theorem.
"""

import argparse
import json
import shutil
import subprocess
from pathlib import Path

import pycparser
from pycparser import c_ast, c_parser

from tools.bbc import cpu_instruction_proof, cycle_reference
from tools.bbc.proofs.cpu_reference import frontend
from tools.bbc.proofs.video_cursor import hashes
from tools.validation.common import ROOT, digest, write_json


def fields(signal):
    result = {}
    position = 256
    for name, width in frontend.FIELDS:
        position -= width
        result[name] = f"{signal}[{position + width - 1}:{position}]"
    return result


def prefix():
    values = {name: "0" for name, _ in frontend.FIELDS}
    values.update(
        {
            "A": "a",
            "X": "x",
            "Y": "y",
            "S": "s",
            "P": "((p & 8'hcf) | 8'h10)",
            "PC": "base+16'd13",
            "bcd_enabled": "8'd1",
        }
    )
    # Every literal has its field width; use parentheses around arithmetic.
    packed = ",".join(
        values[name] if values[name] != "0" else f"{width}'d0" for name, width in frontend.FIELDS
    )
    f = fields("state")
    return (
        """module BbcCpuContractPrefix(
 input clk,input [7:0] a,x,y,s,p,opcode,input [15:0] base,targetAddress,
 input [7:0] targetRead,targetValue,input targetWrite,input [5:0] retirementStep,
 output [5:0] step,output reset,busMatches,prefixReady,writing,instruction,
 output [15:0] address,pc,output [7:0] value,accumulator,indexX,indexY,stack,flags,retiredFlags);
reg initialState=1;
reg [5:0] age=25;
reg [255:0] state=0;
reg [63:0] pins=0;
reg previousFetch=0;
wire [255:0] nextState;
wire [63:0] nextPins;
wire trap;
wire [63:0] response=(pins & ~64'hff0000) | ({56'b0,targetRead}<<16);
M6502ReferenceNext source(.state_in(state),.pins_in(response),.state_out(nextState),.pins_out(nextPins),.trap(trap));
always @(posedge clk)begin
 if(initialState)begin
  state<={"""
        + packed
        + """};
  pins<=64'h3000000 | {48'b0,base+16'd13};
  initialState<=0;
  previousFetch<=0;
 end else begin
  state<=nextState;pins<=nextPins;previousFetch<=pins[25];
 end
 if(age<40)age<=age+1;
end
assign step=age;assign reset=initialState;assign prefixReady=!trap && (initialState || state[173:172]==2'b01);
assign address=pins[15:0];assign writing=!pins[24];assign value=pins[23:16];
assign instruction=previousFetch;
assign busMatches=address==targetAddress && writing==targetWrite && (!writing || value==targetValue);
"""
        + "\n".join(
            f"assign {port}={f[name]}" + (" & 8'hcf" if port == "flags" else "") + ";"
            for port, name in (
                ("pc", "PC"),
                ("accumulator", "A"),
                ("indexX", "X"),
                ("indexY", "Y"),
                ("stack", "S"),
                ("flags", "P"),
            )
        )
        + """
assign retiredFlags=flags;
endmodule
"""
    )


def specialized(expanded, opcodes):
    ast = c_parser.CParser().parse(expanded)
    switches = []

    class Switches(c_ast.NodeVisitor):
        def visit_Switch(self, node):
            switches.append(node)

    Switches().visit(ast)
    if len(switches) != 1:
        raise ValueError("Changed source decoder")
    evaluator = frontend.Frontend(ast)
    switches[0].stmt.block_items = [
        case
        for case in switches[0].stmt.block_items
        if isinstance(case, c_ast.Default)
        or isinstance(case, c_ast.Case)
        and evaluator.case_value(case.expr) >> 3 in set(opcodes) | {0xEA}
    ]
    return frontend.Frontend(ast).generate()


def qualify(selected=None):
    directory = ROOT / "build/bbc/cpu-c-instruction-bridge"
    directory.mkdir(exist_ok=True)
    source = ROOT / "build/bbc/reference-cycle/m6502.h"
    expanded = ROOT / "build/bbc/cpu-c-reference-transpiler/expanded.c"
    native = expanded.with_name("native-comparison-result.json")
    saved = json.loads(native.read_text())
    if not saved.get("passed") or not saved.get("inputs_unchanged"):
        raise ValueError("Current C adapter comparison required")
    for group in ("inputs", "artifacts"):
        if any(digest(ROOT / name) != sha for name, sha in saved[group].items()):
            raise ValueError("Changed C adapter premise")
    original = source.with_name("m6502-original.h")
    if source.read_text() != cycle_reference.derive(original.read_text()):
        raise ValueError("Changed C source derivation")
    scalar = ROOT / "sim/formal/BbcCpuInstructionContract.v"
    control = ROOT / "sim/formal/BbcCpuControlContract.v"
    memory = ROOT / "sim/formal/BbcCpuMemoryContract.v"
    architecture = ROOT / "sim/formal/BbcCpuArchitectureContract.v"
    inventory = ROOT / "tools/bbc/reference/official_stimulus.h"
    groups = cpu_instruction_proof.memory_groups(memory.read_text(), inventory.read_text())
    jobs = []
    for opcode in cpu_instruction_proof.SCALAR_OPCODES:
        jobs.append((opcode, "BbcCpuInstructionContract", scalar.read_text()))
    for opcode in cpu_instruction_proof.CONTROL_OPCODES:
        jobs.append((opcode, "BbcCpuControlContract", control.read_text()))
    for _, opcodes, text in groups:
        for opcode in opcodes:
            jobs.append((opcode, "BbcCpuMemoryContract", text))
    if len(jobs) != 151 or len({j[0] for j in jobs}) != 151:
        raise ValueError("Incomplete opcode domain")
    if selected is not None:
        jobs = [j for j in jobs if j[0] in selected]
    files = [
        Path(__file__),
        Path(frontend.__file__),
        Path(cpu_instruction_proof.__file__),
        Path(cycle_reference.__file__),
        cycle_reference.SOURCE / "lock.json",
        source,
        original,
        expanded,
        native,
        scalar,
        control,
        memory,
        architecture,
        inventory,
        Path(shutil.which("yosys")),
        Path(shutil.which("gcc")),
        *sorted(Path(pycparser.__file__).parent.rglob("*.py")),
    ]
    before = hashes(files)
    unit = directory / "reference-unit-prefix.v"
    unit.write_text(prefix())
    results = []
    for opcode, top, contract in jobs:
        record_directory = directory / f"{opcode:02x}"
        record_directory.mkdir(exist_ok=True)
        case = Path("/tmp/fpgaleet-c-instruction-bridge") / f"{opcode:02x}"
        case.mkdir(parents=True, exist_ok=True)
        model = case / "source.v"
        model.write_text(specialized(expanded.read_text(), [opcode]))
        expectation = case / "contract.v"
        expectation.write_text(contract)
        log = case / "proof.log"
        graph = case / "graph.json"
        witness = case / "counterexample.json"
        witness.unlink(missing_ok=True)
        command = (
            f"read_verilog -formal -sv {model}; read_verilog -formal {expectation} {unit} {architecture}; prep -top {top} -flatten; check -assert; opt; chformal -lower; "
            + f"write_json {graph}; sat -set-assumes -set-init-zero -seq 17 -set opcode {opcode} -prove equivalent 1 -verify -timeout 120 -show equivalent -show opcode -show step -dump_json {witness}"
        )
        with log.open("w") as out:
            run = subprocess.run(
                ["yosys", "-Q", "-T", "-p", command],
                stdout=out,
                stderr=subprocess.STDOUT,
                timeout=180,
            )
        passed = (
            run.returncode == 0
            and "SAT proof finished - no model found: SUCCESS!" in log.read_text()
        )
        if passed:
            cpu_instruction_proof.guard_assumptions(json.loads(graph.read_text()), top)
        record = {
            "opcode": f"{opcode:02x}",
            "passed": passed,
            "inputs": before,
            "artifacts": hashes(
                [model, expectation, unit, log, graph, *([witness] if witness.exists() else [])]
            ),
        }
        write_json(record_directory / "result.json", record)
        results.append(record)
        print(f"C source instruction {opcode:02x}: {'PASS' if passed else 'FAIL'}", flush=True)
        if not passed:
            raise ValueError(f"C source instruction bridge failed: {opcode:02x}")
    stable = before == hashes(files)
    report = {
        "passed": stable,
        "inputs": before,
        "inputs_unchanged": stable,
        "opcodes": [r["opcode"] for r in results],
        "artifacts": hashes([unit, *[directory / r["opcode"] / "result.json" for r in results]]),
        "scope": "Complete admitted source-derived C instruction units against the unchanged architectural/bus contracts used in actual FPGA proofs. All symbolic input registers/flags/PC and bus bytes. Whole decoder cases for the selected opcode and subsequent NOP are retained with original common entry/exit/helpers. Exact read/write address, direction and write byte, retirement PC/registers/flags and duration are checked for 17 cycles including initialized reference unit entry and later NOPs.",
        "limits": "Reference unit starts at an abstract architectural opcode-fetch boundary with the C engine's B/unused flag representation normalized (B=1, unused=0); that representation is also checked throughout each unit.  it does not execute the real C cold/PLP prefix. No FPGA state is forced. IRQ/NMI/warm reset, arbitrary opcode streams, reachability, source-translation correctness and global machine composition remain separate. Custom frontend is a trust boundary with finite native comparison only. No universal CPU/game equivalence.",
        "universal_game_equivalence": False,
    }
    write_json(directory / ("result.json" if selected is None else "selected-result.json"), report)
    if not stable:
        raise ValueError("C source instruction proof inputs changed")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--opcodes", nargs="*", type=lambda value: int(value, 16))
    args = parser.parse_args()
    qualify(None if args.opcodes is None else set(args.opcodes))
