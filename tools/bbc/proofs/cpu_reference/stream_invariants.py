"""Investigate inductive state relations for quiet C short/branch streams."""

import json
import shutil
import subprocess
from pathlib import Path

from tools.bbc.proofs import branch_induction
from tools.bbc.proofs.cpu_reference import eligible_stream, instructions
from tools.bbc.proofs.video_cursor import hashes, replace_once
from tools.validation.common import ROOT, write_json


def strengthen(original):
    fields = instructions.fields("cState")
    history = instructions.fields("cHistory")
    registers = []
    for name, current, following in (
        ("A", "modelA", "nextA"),
        ("X", "modelX", "nextX"),
        ("Y", "modelY", "nextY"),
        ("S", "modelS", "nextS"),
    ):
        registers.append(f"{fields[name]}==(primed ? {following} : {current})")
        registers.append(
            f"(step==26 || {history[name]}==((stage==1 && primed) ? {following} : {current}))"
        )
    relation = " &&\n ".join(registers)
    relation += f""" &&
 (({fields["P"]} & 8'hcf)==(sourceExpectedP & 8'hcf)) &&
 cPreviousFetch==(stage==1) && cPins[25]==(stage==0) &&
 cPins[24] &&
 {fields["PC"]}==(stage==0 ? currentPc : stage==1 ? nextInstructionPc : fallPc) &&
 (stage==0 || {fields["IR"]}==(({{8'b0,stage==1 ? fetchedOpcode : executingOpcode}}<<3)|{{14'b0,stage}})) &&
 (stage<2 || ({fields["AD"]}==targetPc && branch(executingOpcode) && taken(executingOpcode,modelP))) &&
 (stage!=3 || fallPc[15:8]!=targetPc[15:8]) &&
 step>=26 && step<=30 &&
 (primed ? step>=28 : (step==26 && stage==0)||(step==27 && stage==1))"""
    extra = f"""
wire [7:0] completedP=primed ? nextP : modelP;
wire [7:0] sourceExpectedP=completedP;
(* keep *) wire stateRelation={relation};
assign equivalent=observedEquivalent && (reset || stateRelation);
"""
    original = replace_once(
        original,
        "BbcCpuStreamPrefix prefix(",
        "wire [255:0] cState,cHistory;\nwire [63:0] cPins;\nwire cPreviousFetch;\nBbcCpuStreamPrefix prefix(",
    )
    original = replace_once(
        original,
        ".flags(flags),.retiredFlags(retiredFlags));",
        ".flags(flags),.retiredFlags(retiredFlags),.proofState(cState),.proofPins(cPins),.proofHistory(cHistory),.proofPreviousFetch(cPreviousFetch));",
    )
    return replace_once(
        replace_once(
            original,
            "assign equivalent=reset ||",
            "wire observedEquivalent;\nassign observedEquivalent=reset ||",
        ),
        "endmodule",
        extra + "endmodule",
    )


def qualify():
    # These are independently regenerated here; no failed investigation receipt
    # is treated as a premise. Retain every executing source C state driver.
    work = Path("/tmp/fpgaleet-c-stream-invariants")
    work.mkdir(exist_ok=True)
    directory = ROOT / "build/bbc/cpu-c-stream-invariants-investigation"
    directory.mkdir(exist_ok=True)
    previous = Path("/tmp/fpgaleet-c-eligible-stream")
    source, original_prefix = previous / "source.v", previous / "prefix.v"
    original = ROOT / "sim/formal/BbcCpuBranchStreamContract.v"
    architecture = ROOT / "sim/formal/BbcCpuArchitectureContract.v"
    files = [
        Path(__file__),
        Path(eligible_stream.__file__),
        Path(instructions.__file__),
        Path(branch_induction.__file__),
        Path(__file__).parents[1] / "video_cursor.py",
        source,
        original_prefix,
        original,
        architecture,
        Path(shutil.which("yosys")),
    ]
    before = hashes(files)
    prefix = work / "prefix.v"
    prefix.write_text(
        replace_once(
            replace_once(
                original_prefix.read_text(),
                "output [255:0] proofState, output [63:0] proofPins, output proofTrap",
                "output [255:0] proofState, output [63:0] proofPins, output proofTrap, output [255:0] proofHistory, output proofPreviousFetch",
            ),
            "endmodule",
            "assign proofHistory=retirementState;assign proofPreviousFetch=previousFetch;\nendmodule",
        )
    )
    contract = work / "contract.v"
    contract.write_text(strengthen(original.read_text()))
    graph, mapped = work / "graph.json", work / "mapped.json"
    log, witness = work / "proof.log", work / "witness.json"
    witness.unlink(missing_ok=True)
    command = (
        f"read_verilog -formal -sv {source}; read_verilog -formal {prefix} {contract} {architecture}; "
        f"prep -top BbcCpuBranchStreamContract -flatten; check -assert; opt; chformal -lower; write_json {graph}; "
        f"techmap; opt; abc -g AND; opt_clean; write_json {mapped}; "
        "sat -set-assumes -set-init-zero -tempinduct -initsteps 2 -maxsteps 8 -stepsize 1 "
        "-prove equivalent 1 -verify -timeout 120 -show equivalent -show stateRelation "
        "-show prefix.age -show stage -show firstOpcode -show nextOpcode "
        "-show cState -show cHistory -show cPins -show currentPc "
        f"-show fetchedOpcode -show executingOpcode -show modelP -dump_json {witness}"
    )
    with log.open("w") as output:
        run = subprocess.run(
            ["yosys", "-Q", "-T", "-p", command],
            stdout=output,
            stderr=subprocess.STDOUT,
            timeout=600,
        )
    branch_induction.guard_domains(json.loads(graph.read_text()))
    branch_induction.guard_domains(json.loads(mapped.read_text()))
    stable = before == hashes(files)
    passed = stable and run.returncode == 0 and "Induction step proven: SUCCESS!" in log.read_text()
    write_json(
        directory / "result.json",
        {
            "passed": passed,
            "inputs_unchanged": stable,
            "inputs": before,
            "artifacts": hashes([p for p in work.iterdir() if p.is_file()]),
            "scope": "Investigative quiet source stream relation; unchanged architectural/bus checks are strengthened with read-only C state, instruction-stage and architectural retirement relations. The only assumption remains the two-opcode domain. ABC optimizes combinational equations while preserving actual sequential drivers.",
            "limits": "Investigation only until decoder specialization, opcode admission, real source faults and cold-entry composition pass. Arbitrary interrupts, warm reset, enables, memory/control streams, custom C translation and whole-machine equivalence remain open.",
            "universal_game_equivalence": False,
        },
    )
    print("C stream state induction: " + ("PASS" if passed else "FAIL"), flush=True)
    if not passed:
        raise ValueError("C stream state induction incomplete")


if __name__ == "__main__":
    qualify()
