"""Connect a real source reset to fetch pins and retirement observations."""

import re
import shutil
import subprocess
from pathlib import Path

from tools.bbc.proofs import memory_composition
from tools.bbc.proofs.cpu_reference import (
    foundation,
    frontend,
    history_recipe,
    prepared,
    prune,
    reset_sequence,
    short_stream,
    structural,
)
from tools.bbc.proofs.cpu_reference.warm_checks import accepted_result
from tools.bbc.proofs.video_cursor import hashes, replace_once
from tools.validation.common import ROOT, write_json

DIRECTORY = ROOT / "build/bbc/cpu-source-reset-history-handoff"


def contract(prefix, *, fault=None):
    lines = [line for line in prefix.splitlines() if "referenceState<=" in line]
    constructors = [line for line in lines if "hiddenIR" in line]
    if len(constructors) != 1:
        raise ValueError("Changed generalized source fetch constructor")
    entry = constructors[0].split("referenceState<=", 1)[1].split(";referencePins<=", 1)[0]
    values = {
        "hiddenIR": "16'd7",
        "base": "({vectorHigh,vectorLow}-16'd13)",
        "hiddenAD": "{8'd0,vectorLow}",
        "a": "initialState[207:200]",
        "x": "initialState[199:192]",
        "y": "initialState[191:184]",
        "s": "(initialState[183:176]-8'd3)",
        "p": "(initialState[175:168]|8'h14)",
        "hiddenPins": "savedPins",
        "seedPrevious": "previousNmi",
        "seedOlderIrq": "savedState[103:99]",
        "hiddenBranch": "{7'd0,initialPins[26] && !initialState[170]}",
        "seedNmi": "recentNmi",
    }
    if fault == "lost-retained-nmi":
        values["seedNmi"] = "1'b0"
    elif fault == "wrong-previous-nmi":
        values["seedPrevious"] = "!previousNmi"
    elif fault not in (None, "lost-retirement-stack", "lost-fetch-sync"):
        raise ValueError("Unknown reset-history handoff fault")
    entry = re.sub(r"\b(" + "|".join(values) + r")\b", lambda match: values[match[0]], entry)
    text = replace_once(
        reset_sequence.SEQUENCE,
        "reg [255:0] savedState=0;",
        "reg [255:0] savedState=0,retirementState=0;\nreg previousFetch=0;",
    )
    history = "retirementState<=state;previousFetch<=response[25];"
    if fault == "lost-retirement-stack":
        history = "retirementState<=(state&~(256'hff<<176));previousFetch<=response[25];"
    text = replace_once(
        text,
        "phase<=phase+1;savedState<=nextState;",
        "phase<=phase+1;" + history + "savedState<=nextState;",
    )
    fetch = "64'h3000000|{48'd0,{vectorHigh,vectorLow}}"
    if fault == "lost-fetch-sync":
        fetch = "64'h1000000|{48'd0,{vectorHigh,vectorLow}}"
    extra = f"""
wire entryPrecondition=initialState[96:88]==0 && initialState[39:32]==1 && !initialState[173];
wire [255:0] entryState={entry};
wire [63:0] entryPins={fetch};
wire entryMember=savedState==entryState;
wire historyMember=!previousFetch && retirementState[207:176]==entryState[207:176] &&
 retirementState[175:168]==entryState[175:168];
// The first ordinary step overwrites response data and live IRQ/NMI exactly
// as the maintained stream prefix does. Retain all used live control pins.
(* anyseq *) reg [7:0] firstData;
(* anyseq *) reg firstIrq,firstNmi;
wire [63:0] firstResponse=(savedPins&~64'hcff0000)|({{56'd0,firstData}}<<16)|({{63'd0,firstIrq}}<<26)|({{63'd0,firstNmi}}<<27);
wire [63:0] entryResponse=(entryPins&~64'hcff0000)|({{56'd0,firstData}}<<16)|({{63'd0,firstIrq}}<<26)|({{63'd0,firstNmi}}<<27);
wire [255:0] firstState,entryNextState;
wire [63:0] firstPins,entryNextPins;
wire firstTrap,entryTrap;
HandoffNext realFetch(.state_in(savedState),.pins_in(firstResponse),.state_out(firstState),.pins_out(firstPins),.trap(firstTrap));
HandoffNext entryFetch(.state_in(entryState),.pins_in(entryResponse),.state_out(entryNextState),.pins_out(entryNextPins),.trap(entryTrap));
wire [63:0] used=64'h5fffffff;
wire [255:0] stateMask=~({{192'd0,~used}}<<104);
wire firstStepMember=firstTrap==entryTrap && !firstTrap &&
 ((firstState&stateMask)==(entryNextState&stateMask)) && ((firstPins&used)==(entryNextPins&used));
wire handoff=phase!=7 || !entryPrecondition || (entryMember && historyMember && firstStepMember);
"""
    return replace_once(
        text,
        "assign equivalent=stateRelation && (phase==7 || (executingContext && !trap));",
        extra
        + "assign equivalent=stateRelation && (phase==7 || (executingContext && !trap)) && handoff;",
    )


def qualify():
    parent_path = foundation.DIRECTORY / "result.json"
    parent = memory_composition.sealed(parent_path)
    DIRECTORY.mkdir(parents=True, exist_ok=True)
    source = ROOT / "build/bbc/reference-cycle/m6502.h"
    full = foundation.DIRECTORY / "source.v"
    files = [
        Path(__file__),
        parent_path,
        source,
        full,
        *history_recipe.producer_files(),
        *[
            Path(module.__file__)
            for module in (
                foundation,
                frontend,
                prepared,
                prune,
                reset_sequence,
                short_stream,
                structural,
            )
        ],
        Path(__file__).with_name("warm_checks.py"),
        ROOT / "tools/validation/common.py",
        Path(__file__).parents[1] / "video_cursor.py",
        Path(memory_composition.__file__),
        ROOT / "sim/formal/BbcCpuStreamPrefix.v",
        ROOT / "sim/formal/BbcCpuInterruptStreamContract.v",
        ROOT / "tools/bbc/reference/official_stimulus.h",
        Path(shutil.which("gcc")),
        Path(shutil.which("yosys")),
    ]
    before = hashes(files)
    prefix, _ = history_recipe.generate(
        parent["default_native_state"], parent["default_native_pins"]
    )
    (DIRECTORY / "entry-prefix.v").write_text(prefix)
    small = DIRECTORY / "reset.v"
    reset_sequence.decoder(source, small)
    full_copy = DIRECTORY / "full.v"
    full_copy.write_text(
        replace_once(full.read_text(), "module M6502ReferenceNext(", "module Full(")
    )
    comparison = DIRECTORY / "preservation.v"
    comparison.write_text(reset_sequence.PRESERVATION)
    reduced_copy = DIRECTORY / "reduced.v"
    reduced_copy.write_text(
        replace_once(small.read_text(), "module M6502ReferenceNext(", "module Reduced(")
    )
    graph = DIRECTORY / "preservation.json"
    prepared.prepare(
        DIRECTORY,
        f"read_verilog -formal -sv {full_copy} {reduced_copy} {comparison}; prep -top ResetDecoderPreservation -flatten; check -assert; opt; write_json {graph}",
        [full_copy, reduced_copy, comparison],
        [graph],
    )
    if not reset_sequence.run_query(
        graph,
        "ResetDecoderPreservation",
        DIRECTORY / "preservation.log",
        DIRECTORY / "preservation-witness.json",
        "equivalent",
    ):
        raise ValueError("Reset specialization changed a complete-source output")
    # Preserve every defined microstep of the first fetched byte. The complete
    # decoder remains here; input and output normalization is only observed.
    next_model = DIRECTORY / "next.v"
    next_model.write_text(
        replace_once(full.read_text(), "module M6502ReferenceNext(", "module HandoffNext(")
    )
    checks = []
    for name, fault in [
        ("positive", None),
        *[
            (name, name)
            for name in (
                "lost-retained-nmi",
                "wrong-previous-nmi",
                "lost-retirement-stack",
                "lost-fetch-sync",
            )
        ],
    ]:
        case = DIRECTORY / name
        case.mkdir(exist_ok=True)
        property_file, graph, log, witness = [
            case / filename
            for filename in ("contract.v", "graph.json", "proof.log", "witness.json")
        ]
        property_file.write_text(contract(prefix, fault=fault))
        prepared.prepare(
            case,
            f"read_verilog -formal -sv {small} {next_model} {property_file}; prep -top SourceResetSequence -flatten; check -assert; opt; memory_map; opt; techmap; opt; abc -g AND; opt_clean; write_json {graph}",
            [small, next_model, property_file],
            [graph],
            timeout=1800,
        )
        positive = fault is None
        prop = "equivalent" if positive else "faultDefined"
        witness.unlink(missing_ok=True)
        with log.open("w") as output:
            trial = subprocess.run(
                [
                    "yosys",
                    "-Q",
                    "-T",
                    "-p",
                    f"read_json {graph}; sat -set-init-zero -seq 8 -prove {prop} 1 -verify -timeout 300 -show {prop} -show phase -dump_json {witness}",
                ],
                stdout=output,
                stderr=subprocess.STDOUT,
                timeout=900,
            )
        accepted = accepted_result(trial.returncode, log.read_text(), witness, prop, positive)
        checks.append({"name": name, "accepted": accepted})
        print(
            "Source reset/history handoff " + name + (": PASS" if accepted else ": FAIL"),
            flush=True,
        )
        if not accepted:
            break
    memory_composition.sealed(parent_path)
    stable = before == hashes(files)
    passed = stable and len(checks) == 5 and all(check["accepted"] for check in checks)
    artifacts = [
        DIRECTORY / "entry-prefix.v",
        small,
        reduced_copy,
        full_copy,
        next_model,
        comparison,
        DIRECTORY / "preservation.json",
        DIRECTORY / "preservation.log",
        DIRECTORY / "prepared.json",
        DIRECTORY / "prepare.log",
    ]
    artifacts += [p for case in checks for p in (DIRECTORY / case["name"]).glob("*") if p.is_file()]
    write_json(
        DIRECTORY / "result.json",
        {
            "passed": passed,
            "inputs_unchanged": stable,
            "inputs": before,
            "artifacts": hashes(artifacts),
            "checks": checks,
            "scope": "Seven executing source reset ticks map the complete state, live fetch request and read-only retirement architecture to the maintained generalized official-history entry. Arbitrary pre-state, vector/response bytes and IRQ/NMI histories. Canonical bcd-enabled, zero low IRQ history and cleared unused status are implication antecedents. Every first opcode byte is admitted; the actual complete source decoder proves the first response step matches the entry constructor. Only carried unused live-pin/PINS bits are ignored; no architecture, request or used control pin is masked. Four mapping faults require defined source reset histories.",
            "limits": "A source reset-to-history interface theorem, not the history theorem itself or actual RTL/physical-enable composition. Continuous source/RTL warm histories, timing and whole-game equivalence remain separate.",
            "universal_game_equivalence": False,
        },
    )
    if not passed:
        raise ValueError("Source reset/history handoff incomplete")


if __name__ == "__main__":
    qualify()
