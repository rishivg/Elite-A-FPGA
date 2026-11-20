"""Check instruction units with arbitrary preceding C temporary state.

This strengthens the existing bounded source bridge. It does not establish
arbitrary instruction-stream, interrupt or whole-machine equivalence.
"""

import argparse
import json
import re
import shutil
import subprocess
from pathlib import Path

from tools.bbc import cpu_instruction_proof
from tools.bbc.proofs import memory_composition
from tools.bbc.proofs.cpu_reference import frontend, instructions
from tools.bbc.proofs.video_cursor import hashes, replace_once
from tools.validation.common import ROOT, write_json


def prefix():
    original = instructions.prefix()
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
        P="((p & 8'hcf) | 8'h10)",
        bcd_enabled="8'd1",
    )
    initial = re.findall(r"state<=\{[^;]+\};", original)
    if len(initial) != 1:
        raise ValueError("Changed reference instruction initialization")
    original = replace_once(
        original, initial[0], "state<={" + ",".join(values[n] for n, _ in frontend.FIELDS) + "};"
    )
    fields = instructions.fields("state")
    quiet = " && ".join(
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
    # Quiet input pins and a valid opcode-fetch address are initial conditions
    # of this reference unit. Their preservation is a conclusion, not an assume.
    # Old IR/AD/PINS/branch_irq are unconstrained, including reserved pin bits.
    profile = (
        f"({fields['P']} & 8'h30)==8'h10 && {quiet} && "
        f"(age<27 || ({fields['branch_irq']}==0 && {fields['IR']}<16'd2048 && "
        f"{fields['PINS']}==pins)) && "
        f"(pins & ~64'hffffff)==(pins[25] ? 64'h3000000 : 64'h1000000) && "
        f"(!pins[25] || (pins[24] && pins[15:0]=={fields['PC']}))"
    )
    # Writes also occur in this domain: RW must be free when SYNC is low.
    profile = profile.replace(
        "(pins & ~64'hffffff)==(pins[25] ? 64'h3000000 : 64'h1000000)",
        "(pins & ~64'h3ffffff)==0",
    )
    original = replace_once(
        original,
        "assign prefixReady=!trap && (initialState || state[173:172]==2'b01);",
        "assign prefixReady=initialState || (!trap && eligibleProfile);",
    )
    return replace_once(
        original,
        "endmodule",
        "(* anyconst *) reg [15:0] hiddenIR,hiddenAD;\n"
        "(* anyconst *) reg [63:0] hiddenPins;\n"
        "(* anyconst *) reg [7:0] hiddenBranch;\n"
        f"wire eligibleProfile={profile};\nendmodule",
    )


def qualify(selected=None):
    premise = ROOT / "build/bbc/cpu-c-instruction-bridge/result.json"
    saved = memory_composition.sealed(premise)
    opcodes = saved.get("opcodes", [])
    if len(opcodes) != 151 or len(set(opcodes)) != 151:
        raise ValueError("Complete 151-opcode source premise required")
    directory = ROOT / "build/bbc/cpu-c-eligible-instruction-bridge"
    directory.mkdir(exist_ok=True)
    work = Path("/tmp/fpgaleet-c-eligible-instructions")
    work.mkdir(exist_ok=True)
    architecture = ROOT / "sim/formal/BbcCpuArchitectureContract.v"
    records = []
    for opcode in opcodes:
        path = premise.parent / opcode / "result.json"
        child = json.loads(path.read_text())
        if not child.get("passed"):
            raise ValueError("Incomplete original instruction: " + opcode)
        # Child receipts lack inputs_unchanged; their root binds the common
        # inputs and all 151 receipt hashes. Check every child artifact too.
        for category in ("inputs", "artifacts"):
            if child[category] != hashes([ROOT / name for name in child[category]]):
                raise ValueError("Changed original instruction artifacts: " + opcode)
        records.append((opcode, path, child))
    files = [
        Path(__file__),
        Path(instructions.__file__),
        Path(frontend.__file__),
        Path(cpu_instruction_proof.__file__),
        Path(memory_composition.__file__),
        Path(__file__).parents[1] / "video_cursor.py",
        premise,
        architecture,
        Path(shutil.which("yosys")),
        *[path for _, path, _ in records],
    ]
    before = hashes(files)
    unit = work / "prefix.v"
    unit.write_text(prefix())
    results = []
    for opcode, path, child in records:
        if selected is not None and int(opcode, 16) not in selected:
            continue
        previous = Path("/tmp/fpgaleet-c-instruction-bridge") / opcode
        model, expectation = previous / "source.v", previous / "contract.v"
        if not all(str(p) in child["artifacts"] for p in (model, expectation)):
            raise ValueError("Unbound reused instruction model")
        tops = re.findall(r"module (BbcCpu\w+Contract)\(", expectation.read_text())
        if len(tops) != 1:
            raise ValueError("Changed instruction contract top")
        top = tops[0]
        case = work / opcode
        case.mkdir(exist_ok=True)
        log, graph, witness = [case / name for name in ("proof.log", "graph.json", "witness.json")]
        witness.unlink(missing_ok=True)
        command = (
            f"read_verilog -formal -sv {model}; read_verilog -formal {expectation} {unit} {architecture}; "
            f"prep -top {top} -flatten; check -assert; opt; chformal -lower; write_json {graph}; "
            f"sat -set-assumes -set-init-zero -seq 17 -set opcode {int(opcode, 16)} "
            f"-prove equivalent 1 -verify -timeout 120 -show equivalent -show step "
            f"-show prefix.pins -show prefix.state -dump_json {witness}"
        )
        with log.open("w") as output:
            run = subprocess.run(
                ["yosys", "-Q", "-T", "-p", command],
                stdout=output,
                stderr=subprocess.STDOUT,
                timeout=300,
            )
        passed = (
            run.returncode == 0
            and "SAT proof finished - no model found: SUCCESS!" in log.read_text()
        )
        cpu_instruction_proof.guard_assumptions(json.loads(graph.read_text()), top)
        record = {
            "opcode": opcode,
            "passed": passed,
            "premise": str(path),
            "artifacts": hashes(
                [model, expectation, unit, log, graph, *([witness] if witness.exists() else [])]
            ),
        }
        write_json(directory / (opcode + ".json"), record)
        results.append(record)
        print(f"Eligible C instruction {opcode}: {'PASS' if passed else 'FAIL'}", flush=True)
        if not passed:
            break
    stable = before == hashes(files)
    passed = stable and bool(results) and all(r["passed"] for r in results)
    complete = selected is None and len(results) == 151
    report = {
        "passed": passed and (complete or selected is not None),
        "inputs_unchanged": stable,
        "inputs": before,
        "artifacts": hashes([unit, *[directory / (r["opcode"] + ".json") for r in results]]),
        "opcodes": [r["opcode"] for r in results],
        "complete_official_domain": complete,
        "scope": "The unchanged instruction bus/architectural contracts with arbitrary prior C IR, AD, PINS and branch_irq. Architectural registers, meaningful status, PC and bus bytes stay symbolic. Quiet interrupt/BCD/6510 state, reserved-flag representation, overwritten history state and valid future opcode-fetch PC/RW/SYNC are checked conclusions throughout 17 initialized cycles.",
        "limits": "Abstract quiet reference fetch initialization; not actual FPGA state injection. Selected instruction plus NOP successor, bounded duration. Arbitrary successor architecture, real-entry eligibility, interrupt/warm/enable histories, C translation trust and whole-machine composition remain open. Fault controls must be independently extended to this stronger prefix before accepting a composed theorem.",
        "universal_game_equivalence": False,
    }
    write_json(directory / ("result.json" if selected is None else "selected-result.json"), report)
    if not report["passed"]:
        raise ValueError("Eligible instruction investigation incomplete")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--opcodes", nargs="*", type=lambda value: int(value, 16))
    args = parser.parse_args()
    qualify(None if args.opcodes is None else set(args.opcodes))
