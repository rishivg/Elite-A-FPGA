"""Prove the source reset transaction using a preservation-checked decoder."""

import json
import shutil
import subprocess
from pathlib import Path

import pycparser

from tools.bbc.proofs import memory_composition
from tools.bbc.proofs.cpu_reference import frontend, prepared, prune, short_stream, structural
from tools.bbc.proofs.cpu_reference.warm_checks import accepted_result
from tools.bbc.proofs.video_cursor import hashes, replace_once
from tools.validation.common import ROOT, digest, write_json

PRESERVATION = """module ResetDecoderPreservation(output equivalent);
(* anyseq *) reg [255:0] state;
(* anyseq *) reg [63:0] pins;
wire [255:0] fullState,smallState;wire [63:0] fullPins,smallPins;
wire fullTrap,smallTrap;
Full full(.state_in(state),.pins_in(pins),.state_out(fullState),.pins_out(fullPins),.trap(fullTrap));
Reduced small(.state_in(state),.pins_in(pins),.state_out(smallState),.pins_out(smallPins),.trap(smallTrap));
wire context=(pins[25] && pins[30] && !pins[28]) || (!pins[25] && state[255:243]==0);
assign equivalent=!context || (fullState==smallState && fullPins==smallPins && fullTrap==smallTrap);
endmodule
"""


SEQUENCE = """module SourceResetSequence(input clk,output equivalent,output faultDefined);
(* anyconst *) reg [255:0] initialState;
(* anyconst *) reg [63:0] initialPins;
(* anyseq *) reg [7:0] data;
(* anyseq *) reg irqPin,nmiPin;
reg [3:0] phase=0;
reg [255:0] savedState=0;
reg [63:0] savedPins=0;
wire [255:0] state=phase==0 ? initialState : savedState;
wire [63:0] response=phase==0 ? (initialPins|64'h42000000)&~64'h10000000 :
 (savedPins & ~64'h1cff0000) | ({56'd0,data}<<16) |
 ({63'd0,irqPin}<<26) | ({63'd0,nmiPin}<<27);
wire [255:0] nextState;wire [63:0] nextPins;wire trap;
M6502ReferenceNext source(.state_in(state),.pins_in(response),.state_out(nextState),.pins_out(nextPins),.trap(trap));
reg [7:0] vectorLow=0,vectorHigh=0;
reg recentNmi=0,previousNmi=0,definedHistory=1;
always @(posedge clk) if(phase<7)begin
 phase<=phase+1;savedState<=nextState;savedPins<=nextPins;
 definedHistory<=definedHistory && !trap;
 if(phase==5)vectorLow<=data;
 if(phase==6)vectorHigh<=data;
 if(phase==0)recentNmi<=(initialState[57:48]!=0) || (response[27] && !initialState[131]);
 else if(response[27] && !previousNmi)recentNmi<=1;
 previousNmi<=response[27];
end
wire initiallyInterrupted=initialState[41:40]!=0 || initialState[98] || initialState[79:58]!=0;
wire [15:0] oldPc=initialState[239:224];
wire [7:0] oldStack=initialState[183:176];
wire [7:0] expectedStack=oldStack-(phase<2 ? 8'd0:phase==2 ? 8'd1:phase==3 ? 8'd2:8'd3);
wire [15:0] expectedPc=phase==7 ? {vectorHigh,vectorLow} :
 oldPc+(phase>=2 && !initiallyInterrupted ? 16'd1:16'd0);
wire [15:0] expectedAddress=phase==1 ? oldPc : phase==2 ? {8'h01,oldStack} :
 phase==3 ? {8'h01,oldStack-8'd1} : phase==4 ? {8'h01,oldStack-8'd2} :
 phase==5 ? 16'hfffc : phase==6 ? 16'hfffd : {vectorHigh,vectorLow};
wire [7:0] expectedFlags=phase<5 ? initialState[175:168]&8'hef : initialState[175:168]|8'h14;
wire executingContext=(response[25] && response[30] && !response[28]) ||
 (!response[25] && state[255:243]==0);
wire stateRelation=phase<=7 && (phase==0 || (
 savedState[255:240]=={12'd0,phase} && savedState[239:224]==expectedPc &&
 savedState[207:184]==initialState[207:184] && savedState[183:176]==expectedStack &&
 savedState[175:168]==expectedFlags && savedState[31:0]==0 &&
 savedState[39:32]==initialState[39:32] &&
 savedState[167:104]==savedPins && savedPins[15:0]==expectedAddress && savedPins[24] &&
 savedPins[25]==(phase==7) && !savedPins[30] && !savedPins[28] &&
 (phase>=5 ? savedState[47:40]==0 : savedState[42]) &&
 (phase!=4 || savedState[223:208]==16'hfffc) &&
 (phase!=5 || savedState[223:208]==16'hfffd) &&
 (phase<6 || savedState[223:208]=={8'd0,vectorLow}) &&
 (phase!=7 || savedState[79:48]==(recentNmi ? 32'h200:32'd0)) &&
 (phase!=7 || initialState[96:88]!=0 || savedState[98:88]==0)));
assign equivalent=stateRelation && (phase==7 || (executingContext && !trap));
// A counterexample must have a defined entire source history, including the
// completed final fetch where no additional source tick is executed.
wire typedBoolean=initialState[39:32]==0 || initialState[39:32]==1;
assign faultDefined=!typedBoolean || !definedHistory || (phase<7 && trap) || equivalent;
endmodule
"""


def run_query(graph, top, log, witness, property_name, *, sequence=0, positive=True):
    witness.unlink(missing_ok=True)
    temporal = f"-set-init-zero -seq {sequence}" if sequence else ""
    query = f"read_json {graph}; sat {temporal} -prove {property_name} 1 -verify -timeout 300 -show {property_name} -dump_json {witness}"
    with log.open("w") as output:
        run = subprocess.run(
            ["yosys", "-Q", "-T", "-p", query],
            stdout=output,
            stderr=subprocess.STDOUT,
            timeout=900,
        )
    module = json.loads(graph.read_text())["modules"][top]
    if any(c["type"] in ("$assume", "$check") for c in module["cells"].values()):
        raise ValueError("Reset transaction acquired an assumption")
    return accepted_result(run.returncode, log.read_text(), witness, property_name, positive)


def decoder(header, model):
    expanded = subprocess.check_output(
        ["gcc", "-E", "-P", "-"], input=frontend.extract(header), text=True
    )
    # The helper retains NOP as well as BRK. Preservation below checks the
    # complete reset context, including any incoming byte on SYNC + RES.
    model.write_text(prune.prune(structural.generate(short_stream.adapter_ast(expanded, [0])))[0])


def qualify():
    premises = [
        ROOT / "build/bbc" / name / "result.json"
        for name in ("cpu-c-reset-steps", "cpu-c-structural-pruning")
    ]
    reset, complete = [memory_composition.sealed(p) for p in premises]
    full = Path("/tmp/fpgaleet-c-structural-pruning/pruned.v")
    header = ROOT / "build/bbc/reference-cycle/m6502.h"
    if complete["artifacts"].get(str(full)) != digest(full) or reset["inputs"].get(
        str(header.relative_to(ROOT))
    ) != digest(header):
        raise ValueError("Changed qualified complete source decoder/header")
    inputs = [
        Path(__file__),
        *[
            Path(m.__file__)
            for m in (frontend, prepared, prune, short_stream, structural, memory_composition)
        ],
        Path(__file__).with_name("warm_checks.py"),
        Path(__file__).parents[1] / "video_cursor.py",
        *premises,
        header,
        full,
        Path(shutil.which("gcc")),
        Path(shutil.which("yosys")),
        *sorted(Path(pycparser.__file__).parent.rglob("*.py")),
    ]
    before = hashes(inputs)
    work = Path("/tmp/fpgaleet-c-reset-sequence")
    work.mkdir(exist_ok=True)
    small = work / "source.v"
    decoder(header, small)
    full_copy, small_copy, contract = [work / n for n in ("full.v", "reduced.v", "preservation.v")]
    full_copy.write_text(
        replace_once(full.read_text(), "module M6502ReferenceNext(", "module Full(")
    )
    small_copy.write_text(
        replace_once(small.read_text(), "module M6502ReferenceNext(", "module Reduced(")
    )
    contract.write_text(PRESERVATION)
    graph = work / "preservation.json"
    preparation = work / "preservation"
    preparation.mkdir(exist_ok=True)
    command = f"read_verilog -formal -sv {full_copy} {small_copy} {contract}; prep -top ResetDecoderPreservation -flatten; check -assert; opt; write_json {graph}"
    prepared.prepare(preparation, command, [full_copy, small_copy, contract], [graph], timeout=1200)
    log, witness = work / "preservation.log", work / "preservation-witness.json"
    preserved = run_query(graph, "ResetDecoderPreservation", log, witness, "equivalent")
    print("Reset decoder preservation: " + ("PASS" if preserved else "FAIL"), flush=True)
    artifacts = [
        small,
        full_copy,
        small_copy,
        contract,
        graph,
        log,
        preparation / "prepare.log",
        preparation / "prepared.json",
    ]
    if witness.exists():
        artifacts.append(witness)
    checks = [{"name": "decoder-preservation", "accepted": preserved}]
    faults = (
        ("positive", None, None),
        ("wrong-reset-vector", "c->AD=0xFFFC;", "c->AD=0xFFFE;"),
        (
            "lost-stack-decrement",
            "case (0x00<<3)|2: _SAD(0x0100|c->S--,c->PC);",
            "case (0x00<<3)|2: _SAD(0x0100|c->S,c->PC);",
        ),
        ("lost-recent-nmi", "if(c->nmi_pip){c->nmi_pip=0x100;}", "c->nmi_pip=0;"),
        (
            "undefined-reset-dispatch-rejected",
            "                c->IR = 0;",
            "                c->IR = 0xFFFF;",
        ),
    )
    if preserved:
        for name, old, new in faults:
            case = work / name
            case.mkdir(exist_ok=True)
            source = small
            if old:
                altered, source = case / "reference.h", case / "source.v"
                altered.write_text(replace_once(header.read_text(), old, new))
                decoder(altered, source)
                artifacts += [altered, source]
            property_file, graph, log, witness = [
                case / n for n in ("contract.v", "graph.json", "proof.log", "witness.json")
            ]
            property_file.write_text(SEQUENCE)
            command = f"read_verilog -formal -sv {source} {property_file}; prep -top SourceResetSequence -flatten; check -assert; opt; memory_map; opt; write_json {graph}"
            cached = prepared.prepare(case, command, [source, property_file], [graph], timeout=600)
            positive = old is None
            reject_undefined = name == "undefined-reset-dispatch-rejected"
            accepted = run_query(
                graph,
                "SourceResetSequence",
                log,
                witness,
                "equivalent" if positive else "faultDefined",
                sequence=8,
                positive=positive or reject_undefined,
            )
            checks.append({"name": name, "accepted": accepted, "preparation_cache_reused": cached})
            artifacts += [property_file, graph, log, case / "prepare.log", case / "prepared.json"]
            if witness.exists():
                artifacts.append(witness)
            print(
                "Source reset transaction " + name + (": PASS" if accepted else ": FAIL"),
                flush=True,
            )
            if not accepted:
                break
    for p in premises:
        memory_composition.sealed(p)
    stable = before == hashes(inputs)
    passed = stable and len(checks) == 6 and all(c["accepted"] for c in checks)
    write_json(
        ROOT / "build/bbc/cpu-c-reset-sequence/result.json",
        {
            "passed": passed,
            "inputs_unchanged": stable,
            "inputs": before,
            "artifacts": hashes(artifacts),
            "checks": checks,
            "scope": "Complete seven-tick source reset transfer from arbitrary abstract source pre-state and pins at accepted SYNC/RES with RDY clear. Arbitrary per-tick IRQ/NMI and response bytes. Preservation-checked smaller decoder; dummy/stack/vector reads, no writes, register/status/IO preservation, final vector PC and exact recent-NMI retention checked. Three actual C faults require entirely defined source histories with canonical initial C Boolean values; an undefined initial dispatch must not qualify a fault. Post-reset low IRQ pipeline bits vanish when the pre-state has the existing instruction theorem's zero low-bit invariant; canonical BCD-enable boolean preserved.",
            "limits": "A reset transaction lemma over accepted ticks, not arbitrary full instruction histories or a real cold OS entry. No RTL enable/stall, request-queue, real reachability, device/scheduler or full-game composition claim.",
            "universal_game_equivalence": False,
        },
    )
    if not passed:
        raise ValueError("Source reset transaction incomplete")


if __name__ == "__main__":
    qualify()
