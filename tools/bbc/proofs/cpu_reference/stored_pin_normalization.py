"""Prove only previous NMI matters in the source CPU's saved PINS field."""

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

CONTRACT = """module SourceStoredPins(output equivalent,output faultDefined);
(* anyseq *) reg [255:0] state;
(* anyseq *) reg [63:0] pins;
wire [255:0] mask=~(256'hfffffffff7ffffff<<104);
wire [255:0] normalized=state&mask;
wire [255:0] originalState,normalState;
wire [63:0] originalOut,normalOut;wire originalTrap,normalTrap;
M6502ReferenceNext original(.state_in(state),.pins_in(pins),.state_out(originalState),.pins_out(originalOut),.trap(originalTrap));
M6502ReferenceNext normalizedSource(.state_in(normalized),.pins_in(pins),.state_out(normalState),.pins_out(normalOut),.trap(normalTrap));
// The tick replaces saved PINS with returned pins. Previous NMI is the only
// saved pin read beforehand, so every output bit must agree exactly.
assign equivalent=originalState==normalState && originalOut==normalOut && originalTrap==normalTrap;
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
            raise ValueError("Changed complete source saved-pin input")
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
    work = Path("/tmp/fpgaleet-c-stored-pin-normalization")
    work.mkdir(exist_ok=True)
    artifacts, checks = [], []
    for name in ("positive", "saved-res-changes-a", "lost-previous-nmi"):
        case = work / name
        case.mkdir(exist_ok=True)
        source, text = model, CONTRACT
        if name == "saved-res-changes-a":
            altered, source = case / "reference.h", case / "source.v"
            altered.write_text(
                replace_once(
                    header.read_text(),
                    "uint64_t m6502_tick(m6502_t* c, uint64_t pins) {",
                    "uint64_t m6502_tick(m6502_t* c, uint64_t pins) {\n    if(c->PINS & M6502_RES){c->A ^= 1;}",
                )
            )
            expanded = subprocess.check_output(
                ["gcc", "-E", "-P", "-"], input=frontend.extract(altered), text=True
            )
            source.write_text(
                prune.prune(structural.generate(c_parser.CParser().parse(expanded)))[0]
            )
            artifacts += [altered, source]
        elif name == "lost-previous-nmi":
            text = replace_once(text, "256'hfffffffff7ffffff", "256'hffffffffffffffff")
        prop, graph, log, witness = [
            case / n for n in ("contract.v", "graph.json", "proof.log", "witness.json")
        ]
        prop.write_text(text)
        command = f"read_verilog -formal -sv {source} {prop}; prep -top SourceStoredPins -flatten; check -assert; opt; techmap; opt; abc -g AND; opt_clean; write_json {graph}"
        cached = prepared.prepare(case, command, [source, prop], [graph], timeout=1200)
        module = json.loads(graph.read_text())["modules"]["SourceStoredPins"]
        if any(c["type"] in ("$assume", "$check") for c in module["cells"].values()):
            raise ValueError("Saved-pin normalization acquired an assumption")
        positive = name == "positive"
        property_name = "equivalent" if positive else "faultDefined"
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
            trial.returncode, log.read_text(), witness, property_name, positive
        )
        checks.append({"name": name, "accepted": accepted, "preparation_cache_reused": cached})
        artifacts += [prop, graph, log, case / "prepared.json", case / "prepare.log"]
        if witness.exists():
            artifacts.append(witness)
        print(
            "Source saved-pin normalization " + name + (": PASS" if accepted else ": FAIL"),
            flush=True,
        )
        if not accepted:
            break
    memory_composition.sealed(parent)
    stable = before == hashes(inputs)
    passed = stable and len(checks) == 3 and all(c["accepted"] for c in checks)
    write_json(
        ROOT / "build/bbc/cpu-c-stored-pin-normalization/result.json",
        {
            "passed": passed,
            "inputs_unchanged": stable,
            "inputs": before,
            "artifacts": hashes(artifacts),
            "checks": checks,
            "scope": "Universal complete-source-model one-tick equality of all 321 outputs when all saved PINS bits except previous NMI are cleared. Arbitrary state/current pins, no assumptions. An actual C fault depending on saved RES and an incorrect mask dropping previous NMI both require defined-execution counterexamples with canonical C Boolean values.",
            "limits": "Source state normalization only, not a live reset request change or an instruction/RTL/whole-game composition. Independent translation supplies its separate C-typed defined domain.",
            "universal_game_equivalence": False,
        },
    )
    if not passed:
        raise ValueError("Source saved-pin normalization incomplete")


if __name__ == "__main__":
    qualify()
