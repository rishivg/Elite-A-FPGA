"""Check source architecture at the next fetch before its opcode is consumed."""

import argparse
import json
import re
import shutil
import subprocess
from pathlib import Path

from tools.bbc import cpu_instruction_proof
from tools.bbc.proofs import memory_composition
from tools.bbc.proofs.cpu_reference import eligible_instructions, frontend, instructions
from tools.bbc.proofs.video_cursor import hashes, replace_once
from tools.validation.common import ROOT, digest, write_json


def prefix():
    text = replace_once(
        eligible_instructions.prefix(),
        "stack,flags,retiredFlags);",
        "stack,flags,retiredFlags,output [255:0] proofState,output [63:0] proofPins);",
    )
    return replace_once(
        text, "endmodule", "assign proofState=state;assign proofPins=pins;\nendmodule"
    )


def contract(original):
    tops = re.findall(r"module (BbcCpu\w+Contract)\(", original)
    if len(tops) != 1:
        raise ValueError("Changed bus contract inventory")
    top = tops[0]
    if top == "BbcCpuInstructionContract":
        expected = dict(
            A="expectedA",
            X="expectedX",
            Y="expectedY",
            S="isBrk ? s-8'd3 : expectedS",
            P="isBrk ? p|8'h04 : expectedP",
        )
    elif top == "BbcCpuControlContract":
        expected = dict(A="opcode==8'h68 ? operand : a", X="x", Y="y", S="expectedS", P="expectedP")
    elif top == "BbcCpuMemoryContract":
        expected = dict(A="expectedA", X="expectedX", Y="expectedY", S="s", P="expectedP")
    else:
        raise ValueError("Unknown instruction contract")
    fields = instructions.fields("cState")
    checks = [f"{fields[n]}==({expected[n]})" for n in ("A", "X", "Y", "S")]
    checks += [
        f"({fields['P']} & 8'hcf)==(({expected['P']}) & 8'hcf)",
        f"{fields['PC']}==endPc",
        "cPins[25:24]==2'b11",
        "cPins[15:0]==endPc",
    ]
    text = replace_once(
        original,
        "BbcCpuContractPrefix prefix(",
        "wire [255:0] cState;wire [63:0] cPins;\nBbcCpuContractPrefix prefix(",
    )
    text = replace_once(
        text,
        ".flags(flags),.retiredFlags(retiredFlags)",
        ".flags(flags),.retiredFlags(retiredFlags),.proofState(cState),.proofPins(cPins)",
    )
    text = replace_once(
        text,
        "assign equivalent=reset ||",
        "wire observedEquivalent;\nassign observedEquivalent=reset ||",
    )
    text = replace_once(
        text,
        "endmodule",
        "wire fetchFence=step!=retirementStep-6'd1 || ("
        + " && ".join(checks)
        + ");\nassign equivalent=observedEquivalent && (reset || fetchFence);\nendmodule",
    )
    return top, text


def qualify(selected=None):
    premise = ROOT / "build/bbc/cpu-c-instruction-bridge/result.json"
    saved = memory_composition.sealed(premise)
    opcodes = saved.get("opcodes", [])
    if len(opcodes) != 151 or len(set(opcodes)) != 151:
        raise ValueError("Complete source instruction premise required")
    work = Path("/tmp/fpgaleet-c-fetch-boundaries")
    work.mkdir(exist_ok=True)
    directory = ROOT / "build/bbc/cpu-c-fetch-boundaries"
    directory.mkdir(exist_ok=True)
    architecture = ROOT / "sim/formal/BbcCpuArchitectureContract.v"
    files = [
        Path(__file__),
        Path(eligible_instructions.__file__),
        Path(instructions.__file__),
        Path(frontend.__file__),
        Path(cpu_instruction_proof.__file__),
        Path(memory_composition.__file__),
        Path(__file__).parents[1] / "video_cursor.py",
        premise,
        architecture,
        Path(shutil.which("yosys")),
    ]
    before = hashes(files)
    unit = work / "prefix.v"
    unit.write_text(prefix())
    results = []
    for opcode in opcodes:
        if selected is not None and int(opcode, 16) not in selected:
            continue
        child_path = premise.parent / opcode / "result.json"
        child = json.loads(child_path.read_text())
        previous = Path("/tmp/fpgaleet-c-instruction-bridge") / opcode
        model, old_contract = previous / "source.v", previous / "contract.v"
        if not child.get("passed") or any(
            child["artifacts"].get(str(p)) != digest(p) for p in (model, old_contract)
        ):
            raise ValueError("Changed source instruction model")
        case = work / opcode
        case.mkdir(exist_ok=True)
        top, text = contract(old_contract.read_text())
        expectation = case / "contract.v"
        expectation.write_text(text)
        log, graph, witness = [case / n for n in ("proof.log", "graph.json", "witness.json")]
        witness.unlink(missing_ok=True)
        command = (
            f"read_verilog -formal -sv {model}; read_verilog -formal {expectation} {unit} {architecture}; "
            f"prep -top {top} -flatten; check -assert; opt; chformal -lower; write_json {graph}; "
            f"sat -set-assumes -set-init-zero -seq 10 -set opcode {int(opcode, 16)} "
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
        cpu_instruction_proof.guard_assumptions(json.loads(graph.read_text()), top)
        passed = (
            run.returncode == 0
            and "SAT proof finished - no model found: SUCCESS!" in log.read_text()
        )
        record = {
            "opcode": opcode,
            "passed": passed,
            "artifacts": hashes(
                [
                    child_path,
                    model,
                    old_contract,
                    expectation,
                    unit,
                    log,
                    graph,
                    *([witness] if witness.exists() else []),
                ]
            ),
        }
        write_json(directory / (opcode + ".json"), record)
        results.append(record)
        print(f"C fetch boundary {opcode}: {'PASS' if passed else 'FAIL'}", flush=True)
        if not passed:
            break
    stable = before == hashes(files)
    complete = selected is None and len(results) == 151
    passed = (
        stable
        and bool(results)
        and all(r["passed"] for r in results)
        and (complete or selected is not None)
    )
    write_json(
        directory / ("result.json" if selected is None else "selected-result.json"),
        {
            "passed": passed,
            "inputs_unchanged": stable,
            "inputs": before,
            "artifacts": hashes([unit, *[directory / (r["opcode"] + ".json") for r in results]]),
            "opcodes": [r["opcode"] for r in results],
            "complete_official_domain": complete,
            "scope": "Source architecture and exact RW/SYNC/PC pins checked at each selected instruction's first subsequent opcode fetch, before the following opcode is consumed. The original bus/retirement contract remains a conjunct. Arbitrary prior C IR/AD/PINS/branch latch, symbolic architecture and response bytes; preserved quiet profile remains a checked property.",
            "limits": "Bounded 10-cycle reference units. The separate 17-cycle bridge covers later retirement. Decoder specialization, source translation, actual stream composition, fault controls, interrupt/warm/enable histories and whole-game equivalence remain open.",
            "universal_game_equivalence": False,
        },
    )
    if not passed:
        raise ValueError("C fetch-boundary investigation incomplete")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--opcodes", nargs="*", type=lambda v: int(v, 16))
    args = parser.parse_args()
    qualify(None if args.opcodes is None else set(args.opcodes))
