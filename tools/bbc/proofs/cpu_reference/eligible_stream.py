"""Investigate unbounded C streams from a quiet architectural fetch boundary."""

import json
import shutil
import subprocess
from pathlib import Path

import pycparser

from tools.bbc import cpu_instruction_proof
from tools.bbc.proofs import branch_induction, memory_composition
from tools.bbc.proofs.cpu_reference import (
    cold_entry,
    frontend,
    instructions,
    prune,
    short_stream,
    structural,
)
from tools.bbc.proofs.video_cursor import hashes, replace_once
from tools.validation.common import ROOT, write_json


def prefix(initial, pins, original):
    wrapper = short_stream.stream_prefix(initial, pins, original)
    packed = 0
    for name, width in frontend.FIELDS:
        packed = (packed << width) | initial[name]
    values = {name: f"{width}'d0" for name, width in frontend.FIELDS}
    values.update(
        IR="hiddenIR",
        AD="hiddenAD",
        PINS="hiddenPins",
        branch_irq="hiddenBranch",
        PC="base+16'd13",
        A="a",
        X="x",
        Y="y",
        S="s",
        P="((p&8'hcf)|8'h10)",
        bcd_enabled="8'd1",
    )
    seed = "{" + ",".join(values[name] for name, _ in frontend.FIELDS) + "}"
    wrapper = replace_once(wrapper, "reg [3:0] resetEdges = 0;", "reg [3:0] resetEdges = 7;")
    wrapper = replace_once(wrapper, "reg [5:0] age = 0;", "reg [5:0] age = 26;")
    wrapper = replace_once(wrapper, "age <= 0;", "age <= 26;")
    wrapper = replace_once(
        wrapper,
        f"referenceState<=256'h{packed:064x};referencePins<=64'h{pins:016x};previousFetch<=0;",
        f"referenceState<={seed};referencePins<=64'h3000000 | {{48'b0,base+16'd13}};previousFetch<=0;",
    )
    fields = instructions.fields("referenceState")
    invariant = " && ".join(
        f"{fields[name]}=={width}'d{1 if name == 'bcd_enabled' else 0}"
        for name, width in frontend.FIELDS
        if name
        in {
            "irq_pip",
            "nmi_pip",
            "brk_flags",
            "bcd_enabled",
            "io_ddr",
            "io_out",
            "io_inp",
            "io_pins",
        }
    )
    invariant += f" && ({fields['P']} & 8'h30)==8'h10"
    wrapper = replace_once(
        wrapper,
        "assign prefixReady = !trap && (age != 28 || (",
        "assign prefixReady = !trap && entryInvariant && (age != 28 || (",
    )
    wrapper = replace_once(
        wrapper,
        "endmodule",
        "(* anyconst *) reg [15:0] hiddenIR,hiddenAD;\n(* anyconst *) reg [63:0] hiddenPins;\n(* anyconst *) reg [7:0] hiddenBranch;\nwire entryInvariant=reset || ("
        + invariant
        + f") && (!referencePins[25] || (referencePins[24] && referencePins[15:0]=={fields['PC']}));\nendmodule",
    )
    return wrapper


def qualify():
    cold = ROOT / "build/bbc/cpu-c-cold-entry-bridge/result.json"
    initialized = memory_composition.sealed(cold)
    premises = [
        cold,
        ROOT / "build/bbc/cpu-c-structural-adapter/result.json",
        ROOT / "build/bbc/cpu-c-structural-pruning/result.json",
    ]
    for path in premises:
        memory_composition.sealed(path)
    source = ROOT / "build/bbc/reference-cycle/m6502.h"
    expanded = ROOT / "build/bbc/cpu-c-reference-transpiler/expanded.c"
    original_prefix = ROOT / "sim/formal/BbcCpuStreamPrefix.v"
    contract = ROOT / "sim/formal/BbcCpuBranchStreamContract.v"
    architecture = ROOT / "sim/formal/BbcCpuArchitectureContract.v"
    opcodes = sorted(set(cpu_instruction_proof.SCALAR_OPCODES) - {0})
    work = Path("/tmp/fpgaleet-c-eligible-stream")
    work.mkdir(exist_ok=True)
    directory = ROOT / "build/bbc/cpu-c-eligible-stream-investigation"
    directory.mkdir(exist_ok=True)
    files = [
        Path(__file__),
        Path(short_stream.__file__),
        Path(cold_entry.__file__),
        Path(frontend.__file__),
        Path(instructions.__file__),
        Path(prune.__file__),
        Path(structural.__file__),
        Path(branch_induction.__file__),
        Path(memory_composition.__file__),
        Path(cpu_instruction_proof.__file__),
        Path(__file__).parents[1] / "video_cursor.py",
        *premises,
        source,
        expanded,
        original_prefix,
        contract,
        architecture,
        Path(shutil.which("yosys")),
        *sorted(Path(pycparser.__file__).parent.rglob("*.py")),
    ]
    before = hashes(files)
    wrapper = work / "prefix.v"
    wrapper.write_text(
        prefix(
            initialized["default_native_state"],
            initialized["default_native_pins"],
            original_prefix.read_text(),
        )
    )
    rtl = work / "source.v"
    rtl.write_text(
        prune.prune(
            structural.generate(
                short_stream.adapter_ast(expanded.read_text(), opcodes + [0, 0x48, 0x28])
            )
        )[0]
    )
    top = "BbcCpuBranchStreamContract"
    checks = []
    for label, mode in [
        ("bounded", "-seq 16"),
        ("induction", "-tempinduct -initsteps 12 -maxsteps 24 -stepsize 4"),
    ]:
        log = work / (label + ".log")
        graph = work / (label + ".json")
        witness = work / (label + "-counterexample.json")
        witness.unlink(missing_ok=True)
        command = f"read_verilog -formal -sv {rtl}; read_verilog -formal {wrapper} {contract} {architecture}; prep -top {top} -flatten; check -assert; opt; chformal -lower; write_json {graph}; sat -set-assumes -set-init-zero {mode} -prove equivalent 1 -verify -timeout 120 -show equivalent -show prefix.age -show firstOpcode -show nextOpcode -dump_json {witness}"
        with log.open("w") as output:
            run = subprocess.run(
                ["yosys", "-Q", "-T", "-p", command],
                stdout=output,
                stderr=subprocess.STDOUT,
                timeout=600,
            )
        passed = run.returncode == 0 and (
            "SAT proof finished - no model found: SUCCESS!" in log.read_text()
            if label == "bounded"
            else "Induction step proven: SUCCESS!" in log.read_text()
        )
        branch_induction.guard_domains(json.loads(graph.read_text()), top)
        checks.append(
            {
                "name": label,
                "passed": passed,
                "tool_timeout": "proof did time out!" in log.read_text(),
            }
        )
        print(label + (": PASS" if passed else ": FAIL"), flush=True)
        if not passed:
            break
    stable = before == hashes(files)
    passed = stable and len(checks) == 2 and all(check["passed"] for check in checks)
    write_json(
        directory / "investigation-result.json",
        {
            "passed": passed,
            "inputs_unchanged": stable,
            "inputs": before,
            "artifacts": hashes([p for p in work.iterdir() if p.is_file()]),
            "proofs": checks,
            "opcodes": opcodes,
            "scope": "Investigative quiet C streams from an abstract architectural opcode-fetch boundary. Meaningful registers/flags/PC and bus bytes are symbolic; preceding C IR/AD/PINS/branch latch are arbitrary. Reference initialization is one observer edge, not a real reset. Empty interrupt pipelines, default BCD/6510 profile, C reserved-flag representation and fetch PC/RW/SYNC validity are proved invariants. The unchanged short/branch stream contract and sole two-opcode assumption are retained.",
            "limits": "Not accepted until full induction, opcode-domain admission and real source faults pass. Real cold prefix eligibility and arbitrary warm/interrupt/enable histories require separate composition. Structural/C translation trust, remaining instruction classes and full-machine equivalence remain open. No actual FPGA state is assigned.",
            "universal_game_equivalence": False,
        },
    )
    if not passed:
        raise ValueError("C eligible-stream investigation incomplete")


if __name__ == "__main__":
    qualify()
