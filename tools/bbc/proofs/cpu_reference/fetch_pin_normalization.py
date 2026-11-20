"""Prove that unused live pin bits cannot change BBC source CPU behavior."""

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

CONTRACT = """module SourcePinNormalization(output equivalent,output faultDefined);
(* anyseq *) reg [255:0] state;
(* anyseq *) reg [63:0] pins;
// Address/data, RW, SYNC, IRQ, NMI, RDY and RES are the BBC's live CPU pins.
wire [63:0] used=64'h5fffffff;
wire [63:0] normalPins=pins&used;
wire [255:0] originalState,normalState;
wire [63:0] originalOut,normalOut;wire originalTrap,normalTrap;
M6502ReferenceNext original(.state_in(state),.pins_in(pins),.state_out(originalState),.pins_out(originalOut),.trap(originalTrap));
M6502ReferenceNext normalized(.state_in(state),.pins_in(normalPins),.state_out(normalState),.pins_out(normalOut),.trap(normalTrap));
// Only carried unused pins in output pins and saved PINS are normalized.
wire [255:0] stateMask=~({192'd0,~used}<<104);
assign equivalent=originalTrap==normalTrap && (originalOut&used)==(normalOut&used) &&
 (originalState&stateMask)==(normalState&stateMask);
assign faultDefined=originalTrap || normalTrap || state[39:32]>1 || equivalent;
endmodule
"""


def qualify():
    parent = ROOT / "build/bbc/cpu-c-reset-steps/result.json"
    report = memory_composition.sealed(parent)
    model = Path("/tmp/fpgaleet-c-structural-pruning/pruned.v")
    header = ROOT / "build/bbc/reference-cycle/m6502.h"
    for p in (model, header):
        key = str(p.relative_to(ROOT)) if p.is_relative_to(ROOT) else str(p)
        if report["inputs"].get(key) != digest(p):
            raise ValueError("Changed complete source pin-normalization input")
    inputs = [
        Path(__file__),
        *[Path(m.__file__) for m in (frontend, prepared, prune, structural, memory_composition)],
        Path(__file__).with_name("warm_checks.py"),
        Path(__file__).parents[1] / "video_cursor.py",
        parent,
        model,
        header,
        Path(shutil.which("gcc")),
        Path(shutil.which("yosys")),
        *sorted(Path(pycparser.__file__).parent.rglob("*.py")),
    ]
    before = hashes(inputs)
    work = Path("/tmp/fpgaleet-c-fetch-pin-normalization")
    work.mkdir(exist_ok=True)
    artifacts, checks = [], []
    for name, fault in (("positive", False), ("unused-pin-changes-a", True)):
        case = work / name
        case.mkdir(exist_ok=True)
        source = model
        if fault:
            altered, source = case / "reference.h", case / "source.v"
            altered.write_text(
                replace_once(
                    header.read_text(),
                    "uint64_t m6502_tick(m6502_t* c, uint64_t pins) {",
                    "uint64_t m6502_tick(m6502_t* c, uint64_t pins) {\n    if(pins & 0x20000000ULL){c->A ^= 1;}",
                )
            )
            expanded = subprocess.check_output(
                ["gcc", "-E", "-P", "-"], input=frontend.extract(altered), text=True
            )
            source.write_text(
                prune.prune(structural.generate(c_parser.CParser().parse(expanded)))[0]
            )
            artifacts += [altered, source]
        prop, graph, log, witness = [
            case / n for n in ("contract.v", "graph.json", "proof.log", "witness.json")
        ]
        prop.write_text(CONTRACT)
        command = f"read_verilog -formal -sv {source} {prop}; prep -top SourcePinNormalization -flatten; check -assert; opt; techmap; opt; abc -g AND; opt_clean; write_json {graph}"
        cached = prepared.prepare(case, command, [source, prop], [graph], timeout=1200)
        module = json.loads(graph.read_text())["modules"]["SourcePinNormalization"]
        if any(c["type"] in ("$assume", "$check") for c in module["cells"].values()):
            raise ValueError("Pin normalization acquired an assumption")
        property_name = "faultDefined" if fault else "equivalent"
        witness.unlink(missing_ok=True)
        query = f"read_json {graph}; sat -prove {property_name} 1 -verify -timeout 300 -show {property_name} -dump_json {witness}"
        with log.open("w") as output:
            trial = subprocess.run(
                ["yosys", "-Q", "-T", "-p", query],
                stdout=output,
                stderr=subprocess.STDOUT,
                timeout=600,
            )
        accepted = accepted_result(
            trial.returncode, log.read_text(), witness, property_name, not fault
        )
        checks.append({"name": name, "accepted": accepted, "preparation_cache_reused": cached})
        artifacts += [prop, graph, log, case / "prepared.json", case / "prepare.log"]
        if witness.exists():
            artifacts.append(witness)
        print("Source pin normalization " + name + (": PASS" if accepted else ": FAIL"), flush=True)
        if not accepted:
            break
    memory_composition.sealed(parent)
    stable = before == hashes(inputs)
    passed = stable and len(checks) == 2 and all(c["accepted"] for c in checks)
    write_json(
        ROOT / "build/bbc/cpu-c-fetch-pin-normalization/result.json",
        {
            "passed": passed,
            "inputs_unchanged": stable,
            "inputs": before,
            "artifacts": hashes(artifacts),
            "checks": checks,
            "scope": "Universal combinational complete-source-model lemma over arbitrary state and pins, without assumptions. Clearing unused live pin bits preserves trap, every used returned pin and all source state except carried unused pins in saved PINS. Address/data, RW/SYNC, IRQ/NMI, RDY and RES are never masked. An actual-C unused-pin corruption of A requires a defined-execution counterexample with a canonical C Boolean value.",
            "limits": "Local source pin-normalization lemma, not retirement-model alignment, reset/RTL enable scheduling or full-game composition. Defined C-type scope comes from the independent translation premise.",
            "universal_game_equivalence": False,
        },
    )
    if not passed:
        raise ValueError("Source pin normalization incomplete")


if __name__ == "__main__":
    qualify()
