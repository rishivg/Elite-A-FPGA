"""Prove source RES isolation and SYNC entry without enumerating histories."""

import json
import shutil
import subprocess
from pathlib import Path

import pycparser
from pycparser import c_parser

from tools.bbc.proofs import memory_composition
from tools.bbc.proofs.cpu_reference import frontend, prepared, prune, structural
from tools.bbc.proofs.cpu_reference.warm_checks import accepted_result
from tools.bbc.proofs.video_cursor import hashes, replace_once
from tools.validation.common import ROOT, digest, write_json

CONTRACT = """module SourceResetSteps(output equivalent,output faultObservable);
(* anyseq *) reg [255:0] state;
(* anyseq *) reg [63:0] pins;
(* anyseq *) reg [7:0] alternateData;
wire [63:0] quietPins=pins & ~64'h42000000;
wire [63:0] waitingPins=quietPins | 64'h40000000;
wire [63:0] startPins=(pins | 64'h42000000) & ~64'h10000000;
wire [63:0] alternatePins=(startPins & ~64'hff0000) | ({56'd0,alternateData}<<16);
wire [255:0] quietState,waitingState,startState,alternateState;
wire [63:0] quietOut,waitingOut,startOut,alternateOut;
wire quietTrap,waitingTrap,startTrap,alternateTrap;
M6502ReferenceNext quiet(.state_in(state),.pins_in(quietPins),.state_out(quietState),.pins_out(quietOut),.trap(quietTrap));
M6502ReferenceNext waiting(.state_in(state),.pins_in(waitingPins),.state_out(waitingState),.pins_out(waitingOut),.trap(waitingTrap));
M6502ReferenceNext start(.state_in(state),.pins_in(startPins),.state_out(startState),.pins_out(startOut),.trap(startTrap));
M6502ReferenceNext alternate(.state_in(state),.pins_in(alternatePins),.state_out(alternateState),.pins_out(alternateOut),.trap(alternateTrap));
// Outside SYNC, RES is only carried in output pins and their saved PINS field.
wire inert=((quietState & ~(256'd1<<134))==(waitingState & ~(256'd1<<134))) &&
 ((quietOut & ~64'h40000000)==(waitingOut & ~64'h40000000)) && quietTrap==waitingTrap;
// SYNC + RES forces BRK regardless of the incoming opcode. Returned read-data
// bits remain the supplied data in pins and saved PINS, so compare them masked.
wire opcodeIndependent=((startState & ~(256'hff<<120))==(alternateState & ~(256'hff<<120))) &&
 ((startOut & ~64'hff0000)==(alternateOut & ~64'hff0000)) && !startTrap && !alternateTrap;
wire entry=startState[255:240]==16'd1 && startState[239:224]==state[239:224] &&
 startState[207:176]==state[207:176] &&
 (startState[175:168]&8'hcf)==(state[175:168]&8'hcf) && startState[31:0]==0 &&
 startOut[15:0]==state[239:224] && startOut[24] && !startOut[25] && !startOut[30];
assign equivalent=inert && opcodeIndependent && entry;
// A negative control must fail with all four source executions defined.
assign faultObservable=quietTrap || waitingTrap || startTrap || alternateTrap || equivalent;
endmodule
"""


def qualify():
    premises = [
        ROOT / "build/bbc" / name / "result.json"
        for name in (
            "cpu-c-structural-pruning",
            "cpu-c-structural-adapter",
            "cpu-c-independent-translation",
        )
    ]
    full_bound, source_bound, translation = [memory_composition.sealed(path) for path in premises]
    full = Path("/tmp/fpgaleet-c-structural-pruning/pruned.v")
    source = ROOT / "build/bbc/reference-cycle/m6502.h"
    if full_bound["artifacts"].get(str(full)) != digest(full) or source_bound["inputs"].get(
        str(source.relative_to(ROOT))
    ) != digest(source):
        raise ValueError("Changed complete source model/header")
    for path in (full, source):
        name = str(path) if path == full else str(path.relative_to(ROOT))
        if translation["inputs"].get(name) != digest(path):
            raise ValueError("Independent translation uses a different source model/header")
    inputs = [
        Path(__file__),
        *[
            Path(module.__file__)
            for module in (frontend, prepared, prune, structural, memory_composition)
        ],
        Path(__file__).with_name("warm_checks.py"),
        Path(__file__).parents[1] / "video_cursor.py",
        *premises,
        full,
        source,
        Path(shutil.which("gcc")),
        Path(shutil.which("yosys")),
        *sorted(Path(pycparser.__file__).parent.rglob("*.py")),
    ]
    before = hashes(inputs)
    work = Path("/tmp/fpgaleet-c-reset-steps-split")
    work.mkdir(exist_ok=True)
    faults = (
        ("inert", None, None),
        ("opcodeIndependent", None, None),
        ("entry", None, None),
        (
            "res-corrupts-in-flight-a",
            "uint64_t m6502_tick(m6502_t* c, uint64_t pins) {",
            "uint64_t m6502_tick(m6502_t* c, uint64_t pins) {\n    if(pins & M6502_RES){c->A ^= 1;}",
        ),
        (
            "reset-keeps-fetched-opcode",
            "                c->IR = 0;",
            "                c->IR = _GD()<<3;",
        ),
    )
    artifacts, checks = [], []
    for name, old, new in faults:
        case = work / name
        case.mkdir(exist_ok=True)
        contract = case / "contract.v"
        positive = old is None
        text = CONTRACT
        if positive:
            text = replace_once(
                text, "output equivalent,output faultObservable", "output equivalent"
            )
            text = replace_once(
                text,
                "assign equivalent=inert && opcodeIndependent && entry;",
                f"assign equivalent={name};",
            )
            text = replace_once(
                text,
                "assign faultObservable=quietTrap || waitingTrap || startTrap || alternateTrap || equivalent;",
                "",
            )
        contract.write_text(text)
        artifacts.append(contract)
        model = full
        if old:
            header, model = case / "reference.h", case / "source.v"
            header.write_text(replace_once(source.read_text(), old, new))
            expanded = subprocess.check_output(
                ["gcc", "-E", "-P", "-"], input=frontend.extract(header), text=True
            )
            model.write_text(
                prune.prune(structural.generate(c_parser.CParser().parse(expanded)))[0]
            )
            artifacts += [header, model]
        graph, log, witness = [case / n for n in ("graph.json", "proof.log", "witness.json")]
        witness.unlink(missing_ok=True)
        command = f"read_verilog -formal -sv {model} {contract}; prep -top SourceResetSteps -flatten; check -assert; opt; techmap; opt; abc -g AND; opt_clean; write_json {graph}"
        cached = prepared.prepare(case, command, [model, contract], [graph], timeout=1200)
        module = json.loads(graph.read_text())["modules"]["SourceResetSteps"]
        if any(cell["type"] in ("$assume", "$check") for cell in module["cells"].values()):
            raise ValueError("Source reset lemma acquired an assumption")
        property_name = "equivalent" if positive else "faultObservable"
        shows = " ".join(
            "-show " + name
            for name in (property_name, "state", "pins", "alternateData")
            if name in module["netnames"]
        )
        query = f"read_json {graph}; sat -prove {property_name} 1 -verify -timeout 300 {shows} -dump_json {witness}"
        with log.open("w") as output:
            run = subprocess.run(
                ["yosys", "-Q", "-T", "-p", query],
                stdout=output,
                stderr=subprocess.STDOUT,
                timeout=900,
            )
        accepted = accepted_result(
            run.returncode, log.read_text(), witness, property_name, positive
        )
        checks.append({"name": name, "accepted": accepted, "preparation_cache_reused": cached})
        artifacts += [graph, log, case / "prepared.json", case / "prepare.log"]
        if witness.exists():
            artifacts.append(witness)
        print("Source reset step " + name + (": PASS" if accepted else ": FAIL"), flush=True)
        if not accepted:
            break
    for premise in premises:
        memory_composition.sealed(premise)
    stable = before == hashes(inputs)
    passed = stable and len(checks) == len(faults) and all(c["accepted"] for c in checks)
    write_json(
        ROOT / "build/bbc/cpu-c-reset-steps/result.json",
        {
            "passed": passed,
            "inputs_unchanged": stable,
            "inputs": before,
            "artifacts": hashes(artifacts),
            "checks": checks,
            "scope": "Universal single-step source-model RES isolation outside SYNC and opcode-independent SYNC reset entry. Complete preservation-proved C decoder; arbitrary input state/pins/data with no assumptions. Only carried RES and returned read-data fields are masked in the respective comparisons. Entry architecture and reset pins checked. Two actual-C mutations require defined-execution counterexamples.",
            "limits": "Source transition lemmas, not initialized reset-vector histories, RTL composition, C-type domain closure or whole-game equivalence. The independent C translation proof has its own defined-input scope.",
            "universal_game_equivalence": False,
        },
    )
    if not passed:
        raise ValueError("Source reset transition lemmas incomplete")


if __name__ == "__main__":
    qualify()
