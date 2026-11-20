"""Investigate source-derived C short/branch streams against the ISA model."""

import argparse
import json
import shutil
import subprocess
from pathlib import Path

import pycparser
from pycparser import c_ast, c_parser

from tools.bbc import cpu_instruction_proof
from tools.bbc.proofs import branch_induction, memory_composition
from tools.bbc.proofs.cpu_reference import cold_entry, frontend, instructions, prune, structural
from tools.bbc.proofs.video_cursor import hashes, replace_once
from tools.validation.common import ROOT, digest, write_json


def stream_prefix(initial, pins, original):
    packed = 0
    for name, width in frontend.FIELDS:
        packed = (packed << width) | initial[name]
    wrapper = cold_entry.prefix(original, packed, pins)
    wrapper = replace_once(wrapper, "module CColdPrefix(", "module BbcCpuStreamPrefix(")
    wrapper = replace_once(wrapper, "end else if(age<26) begin", "end else begin")
    wrapper = replace_once(wrapper, "if (age < 26)", "if (age < 30)")
    # The pin engine commits arithmetic at its fetch-producing tick. The RTL
    # contract observes retirement after the following decode. This is a
    # read-only architectural snapshot; the executing C state is unchanged.
    fields = instructions.fields("retirementState")
    wrapper = replace_once(
        wrapper,
        "  reg previousFetch=0;",
        "  reg previousFetch=0;\n  reg [255:0] retirementState=0;\n  always @(posedge clk) retirementState<=referenceState;",
    )
    current = instructions.fields("referenceState")
    # C advances PC when requesting an immediate/branch operand. The RTL
    # diagnostic PC at retirement instead points one byte past that fetch.
    # Its operand/dummy request address provides that read-only observation.
    wrapper = replace_once(
        wrapper,
        f"assign pc={current['PC']};",
        f"assign pc=previousFetch ? referencePins[15:0] : {current['PC']};",
    )
    for port, name in (("accumulator", "A"), ("indexX", "X"), ("indexY", "Y"), ("stack", "S")):
        wrapper = replace_once(
            wrapper, f"assign {port}={current[name]};", f"assign {port}={fields[name]};"
        )
    wrapper = replace_once(
        wrapper,
        f"assign flags={current['P']};",
        f"wire earlyI=({current['IR']}>>3)==8'h58 || ({current['IR']}>>3)==8'h78;\n  assign flags=(earlyI ? ({fields['P']} & 8'hfb) | ({current['P']} & 8'h04) : {fields['P']});",
    )
    wrapper = replace_once(
        wrapper,
        "assign prefixReady = age != 28 || (",
        "assign prefixReady = !trap && (age != 28 || (",
    )
    wrapper = replace_once(
        wrapper,
        "(flags & 8'hcf) == (initialP & 8'hcf)\n  );",
        "(flags & 8'hcf) == (initialP & 8'hcf)\n  ));",
    )
    return wrapper


def adapter_ast(expanded, opcodes):
    ast = c_parser.CParser().parse(expanded)
    switches = []

    class Switches(c_ast.NodeVisitor):
        def visit_Switch(self, node):
            switches.append(node)

    Switches().visit(ast)
    if len(switches) != 1:
        raise ValueError("Changed source decoder inventory")
    evaluator = frontend.Frontend(ast)
    switches[0].stmt.block_items = [
        case
        for case in switches[0].stmt.block_items
        if isinstance(case, c_ast.Default)
        or isinstance(case, c_ast.Case)
        and evaluator.case_value(case.expr) >> 3 in set(opcodes) | {0xEA}
    ]
    return ast


def qualify(bounded_only=False):
    cold = ROOT / "build/bbc/cpu-c-cold-entry-bridge/result.json"
    saved = memory_composition.sealed(cold)
    structural_premise = ROOT / "build/bbc/cpu-c-structural-adapter/result.json"
    pruning_premise = ROOT / "build/bbc/cpu-c-structural-pruning/result.json"
    memory_composition.sealed(structural_premise)
    memory_composition.sealed(pruning_premise)
    source = ROOT / "build/bbc/reference-cycle/m6502.h"
    expanded = ROOT / "build/bbc/cpu-c-reference-transpiler/expanded.c"
    original_prefix = ROOT / "sim/formal/BbcCpuStreamPrefix.v"
    contract = ROOT / "sim/formal/BbcCpuBranchStreamContract.v"
    architecture = ROOT / "sim/formal/BbcCpuArchitectureContract.v"
    opcodes = sorted(set(cpu_instruction_proof.SCALAR_OPCODES) - {0})
    if len(opcodes) != 41:
        raise ValueError("Changed short/branch opcode domain")
    work = Path("/tmp/fpgaleet-c-short-stream")
    work.mkdir(exist_ok=True)
    directory = ROOT / "build/bbc/cpu-c-short-stream-investigation"
    directory.mkdir(exist_ok=True)
    files = [
        Path(__file__),
        Path(cold_entry.__file__),
        Path(frontend.__file__),
        Path(instructions.__file__),
        Path(structural.__file__),
        Path(prune.__file__),
        Path(branch_induction.__file__),
        Path(memory_composition.__file__),
        Path(cpu_instruction_proof.__file__),
        Path(__file__).parents[1] / "video_cursor.py",
        cold,
        structural_premise,
        pruning_premise,
        source,
        expanded,
        original_prefix,
        contract,
        architecture,
        Path(shutil.which("gcc")),
        Path(shutil.which("yosys")),
        *sorted(Path(pycparser.__file__).parent.rglob("*.py")),
    ]
    before = hashes(files)
    wrapper = work / "prefix.v"
    wrapper.write_text(
        stream_prefix(
            saved["default_native_state"], saved["default_native_pins"], original_prefix.read_text()
        )
    )
    rtl = work / "source.v"
    rtl.write_text(
        prune.prune(
            structural.generate(adapter_ast(expanded.read_text(), opcodes + [0, 0x48, 0x28]))
        )[0]
    )
    # Cache only the parsed C adapter, whose inputs are independent of the
    # retirement observer and property. Read RTLIL retains the same processes;
    # it skips repeated expensive SystemVerilog frontend work.
    parsed = work / "parsed-model.il"
    cache = work / "parsed-model-receipt.json"
    parse_log = work / "parse-model.log"
    parse_command = f"read_verilog -formal -sv -noopt {rtl}; write_rtlil {parsed}"
    cache_inputs = {"files": hashes([rtl, Path(shutil.which("yosys"))]), "command": parse_command}
    cached = json.loads(cache.read_text()) if cache.exists() else {}
    if not (
        cached.get("inputs") == cache_inputs
        and parsed.is_file()
        and cached.get("rtlil_sha256") == digest(parsed)
        and parse_log.is_file()
        and cached.get("log_sha256") == digest(parse_log)
    ):
        with parse_log.open("w") as output:
            subprocess.run(
                ["yosys", "-Q", "-T", "-p", parse_command],
                stdout=output,
                stderr=subprocess.STDOUT,
                check=True,
                timeout=600,
            )
        write_json(
            cache,
            {
                "inputs": cache_inputs,
                "rtlil_sha256": digest(parsed),
                "log_sha256": digest(parse_log),
            },
        )
    top = "BbcCpuBranchStreamContract"
    results = []
    modes = [("bounded", "-seq 44")]
    if not bounded_only:
        modes.append(("induction", "-tempinduct -initsteps 44 -maxsteps 64 -stepsize 4"))
    for label, mode in modes:
        log = work / (label + ".log")
        graph = work / (label + ".json")
        witness = work / (label + "-counterexample.json")
        witness.unlink(missing_ok=True)
        command = f"read_rtlil {parsed}; read_verilog -formal {wrapper} {contract} {architecture}; prep -top {top} -flatten; check -assert; opt; chformal -lower; write_json {graph}; sat -set-assumes -set-init-zero {mode} -prove equivalent 1 -verify -timeout 120 -show equivalent -show prefix.age -show firstOpcode -show nextOpcode -dump_json {witness}"
        with log.open("w") as output:
            run = subprocess.run(
                ["yosys", "-Q", "-T", "-p", command],
                stdout=output,
                stderr=subprocess.STDOUT,
                timeout=600,
            )
        passed = run.returncode == 0 and (
            "Induction step proven: SUCCESS!" in log.read_text()
            if label == "induction"
            else "SAT proof finished - no model found: SUCCESS!" in log.read_text()
        )
        branch_induction.guard_domains(json.loads(graph.read_text()), top)
        results.append({"name": label, "passed": passed})
        print(label + (": PASS" if passed else ": FAIL"), flush=True)
        if not passed:
            write_json(
                directory / "investigation-failure.json",
                {
                    "passed": False,
                    "inputs": before,
                    "inputs_unchanged": before == hashes(files),
                    "artifacts": hashes([p for p in work.iterdir() if p.is_file()]),
                    "proofs": results,
                    "universal_game_equivalence": False,
                },
            )
            raise ValueError("Source C short/branch stream investigation failed: " + label)
    stable = before == hashes(files)
    write_json(
        directory / ("bounded-result.json" if bounded_only else "investigation-result.json"),
        {
            "passed": stable,
            "inputs_unchanged": stable,
            "inputs": before,
            "artifacts": hashes([p for p in work.iterdir() if p.is_file()]),
            "proofs": results,
            "opcodes": opcodes,
            "scope": "Source-derived short/branch C stream investigation using the unchanged architectural/bus contract and real quiet reset/register/PLP prefix. An architectural retirement snapshot aligns the C engine's earlier commit with the RTL contract without changing executing state. The sole assumption restricts only the two symbolic opcode slots.",
            "limits": "Investigation only until opcode-domain admission and actual source faults are checked. C translation, interrupt/warm histories, memory/control instruction streams, guest reachability and whole-machine equivalence remain separate.",
            "universal_game_equivalence": False,
        },
    )
    if not stable:
        raise ValueError("C short-stream investigation inputs changed")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bounded-only", action="store_true")
    qualify(parser.parse_args().bounded_only)
