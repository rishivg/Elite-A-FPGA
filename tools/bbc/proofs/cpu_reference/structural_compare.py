"""Check structural lowering against procedural RTL and native maintained C."""

import json
import shutil
import subprocess
from pathlib import Path

import pycparser
from pycparser import c_ast, c_parser

from tools.bbc.proofs import memory_composition
from tools.bbc.proofs.cpu_reference import compare, frontend, structural
from tools.bbc.proofs.video_cursor import hashes, replace_once
from tools.validation.common import ROOT, digest, write_json


def selected_ast(expanded, opcode):
    ast = c_parser.CParser().parse(expanded)
    switches = []

    class Switches(c_ast.NodeVisitor):
        def visit_Switch(self, node):
            switches.append(node)

    Switches().visit(ast)
    if len(switches) != 1:
        raise ValueError("Changed original C decoder")
    evaluator = frontend.Frontend(ast)
    switches[0].stmt.block_items = [
        case
        for case in switches[0].stmt.block_items
        if isinstance(case, c_ast.Default)
        or isinstance(case, c_ast.Case)
        and evaluator.case_value(case.expr) >> 3 in {opcode, 0xEA}
    ]
    return ast


def qualify():
    premise = ROOT / "build/bbc/cpu-c-reference-transpiler/native-comparison-result.json"
    memory_composition.sealed(premise)
    directory = ROOT / "build/bbc/cpu-c-structural-adapter"
    directory.mkdir(exist_ok=True)
    work = Path("/tmp/fpgaleet-c-structural-reference")
    work.mkdir(exist_ok=True)
    native = work / "native"
    native.mkdir(exist_ok=True)
    source = ROOT / "build/bbc/reference-cycle/m6502.h"
    expanded = ROOT / "build/bbc/cpu-c-reference-transpiler/expanded.c"
    header = expanded.with_name("reference_fields.h")
    harness = Path(compare.__file__).with_name("compare.cpp")
    files = [
        Path(__file__),
        Path(structural.__file__),
        Path(frontend.__file__),
        Path(compare.__file__),
        Path(memory_composition.__file__),
        Path(__file__).parents[1] / "video_cursor.py",
        premise,
        source,
        expanded,
        header,
        harness,
        Path(shutil.which("verilator")),
        Path(shutil.which("c++")),
        Path(shutil.which("yosys")),
        *sorted(Path(pycparser.__file__).parent.rglob("*.py")),
    ]
    before = hashes(files)
    model = work / "full.v"
    model.write_text(structural.generate(c_parser.CParser().parse(expanded.read_text())))
    includes = native / "reference-include"
    includes.mkdir(exist_ok=True)
    for path in (source, header):
        shutil.copyfile(path, includes / path.name)
        if digest(path) != digest(includes / path.name):
            raise ValueError("Changed independent native header copy")
    build_log = work / "native-build.log"
    command = [
        "verilator",
        "-O0",
        "--cc",
        "--exe",
        "--build",
        "-j",
        "1",
        "--top-module",
        "M6502ReferenceNext",
        "--Mdir",
        str(native),
        "--output-split",
        "5000",
        "--output-split-cfuncs",
        "1000",
        "-Wno-fatal",
        "-CFLAGS",
        f"-O1 -std=c++17 -I{includes}",
        str(model),
        str(harness),
    ]
    build_inputs = {
        "command": command,
        "files": hashes(
            [
                model,
                source,
                header,
                harness,
                *includes.glob("*.h"),
                Path(shutil.which("verilator")),
                Path(shutil.which("c++")),
            ]
        ),
    }
    cache = native / "build-receipt.json"
    binary = native / "VM6502ReferenceNext"
    saved = json.loads(cache.read_text()) if cache.exists() else {}
    if not (
        saved.get("inputs") == build_inputs
        and binary.is_file()
        and saved.get("binary_sha256") == digest(binary)
        and build_log.is_file()
        and saved.get("log_sha256") == digest(build_log)
    ):
        with build_log.open("w") as output:
            subprocess.run(
                command, stdout=output, stderr=subprocess.STDOUT, check=True, timeout=600
            )
        write_json(
            cache,
            {
                "inputs": build_inputs,
                "binary_sha256": digest(binary),
                "log_sha256": digest(build_log),
            },
        )
    result = subprocess.run([str(binary), "100000"], capture_output=True, text=True, timeout=120)
    (work / "native-comparison.log").write_text(result.stdout + result.stderr)
    result.check_returncode()
    if (
        result.stdout.strip()
        != "100000 independent C tick transitions; all 256 state bits and 64 returned pins agree"
    ):
        raise ValueError("Incomplete full structural native C comparison")
    print(result.stdout.strip(), flush=True)
    checks = []
    for opcode in (0x18, 0x69, 0xE9, 0x06, 0x6C, 0x28, 0x00, 0x10):
        case = work / f"{opcode:02x}"
        case.mkdir(exist_ok=True)
        ast = selected_ast(expanded.read_text(), opcode)
        procedural = case / "procedural.v"
        procedural.write_text(
            replace_once(
                frontend.Frontend(ast).generate(),
                "module M6502ReferenceNext(",
                "module ProceduralReference(",
            )
        )
        lowered = case / "structural.v"
        lowered.write_text(
            replace_once(
                structural.generate(ast),
                "module M6502ReferenceNext(",
                "module StructuralReference(",
            )
        )
        contract = case / "contract.v"
        contract.write_text("""module AdapterEquality(input [255:0] state,input [63:0] pins,output equivalent);
wire [255:0] a,b;wire [63:0] x,y;wire u,v;
ProceduralReference original(.state_in(state),.pins_in(pins),.state_out(a),.pins_out(x),.trap(u));
StructuralReference lowered(.state_in(state),.pins_in(pins),.state_out(b),.pins_out(y),.trap(v));
assign equivalent=a==b && x==y && u==v;
endmodule
""")
        log = case / "proof.log"
        proof = f"read_verilog -formal -sv {procedural} {lowered} {contract}; prep -top AdapterEquality -flatten; check -assert; opt; write_json {case}/graph.json; sat -prove equivalent 1 -verify -timeout 120 -show equivalent -dump_json {case}/counterexample.json"
        with log.open("w") as output:
            run = subprocess.run(
                ["yosys", "-Q", "-T", "-p", proof],
                stdout=output,
                stderr=subprocess.STDOUT,
                timeout=240,
            )
        passed = (
            run.returncode == 0
            and "SAT proof finished - no model found: SUCCESS!" in log.read_text()
        )
        checks.append({"opcode": f"{opcode:02x}", "passed": passed})
        print(f"Structural/procedural {opcode:02x}: {'PASS' if passed else 'FAIL'}", flush=True)
        if not passed:
            raise ValueError("Structural lowering differs from procedural adapter")
    stable = before == hashes(files)
    write_json(
        directory / "result.json",
        {
            "passed": stable,
            "inputs_unchanged": stable,
            "inputs": before,
            "artifacts": hashes(
                [p for p in work.rglob("*") if p.is_file() and not p.is_relative_to(native)]
                + [binary, cache, *includes.glob("*.h")]
            ),
            "native_comparison_transitions": 100000,
            "procedural_structural_checks": checks,
            "scope": "The full structural C adapter independently agrees with native maintained C for 100000 transitions over all admitted microcode positions. Eight selected instruction-plus-NOP adapters prove bit-for-bit equality with the procedural translation for all 256 input state bits and 64 input pins, including every output state/pin/trap bit, with no assumptions.",
            "limits": "Native samples and eight formal decoder selections do not prove lowering correctness for the complete decoder or C translation correctness. Additional selections and source stream/global composition remain separate. This is an investigation adapter; existing instruction/RTL receipts are unchanged.",
            "universal_game_equivalence": False,
        },
    )
    if not stable:
        raise ValueError("Structural adapter validation inputs changed")


if __name__ == "__main__":
    qualify()
