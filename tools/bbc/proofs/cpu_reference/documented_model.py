"""Prove a smaller source decoder preserves every documented execution context."""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pycparser

from tools.bbc.proofs import memory_composition
from tools.bbc.proofs.cpu_reference import frontend, prepared, prune, short_stream, structural
from tools.bbc.proofs.video_cursor import hashes
from tools.validation.common import ROOT, digest, write_json


def inventory():
    path = ROOT / "tools/bbc/reference/official_stimulus.h"
    values = sorted(int(n, 16) for n in re.findall(r"\{0x([0-9a-f]{2}),", path.read_text()))
    if len(values) != 151 or len(set(values)) != 151 or 0 not in values:
        raise ValueError("Changed complete official opcode inventory")
    return values


def context_function(opcodes):
    cases = ",".join("8'h%02x" % n for n in opcodes)
    return f"""function documented;
 input [7:0] opcode;
 begin case(opcode)
 {cases}:documented=1;
 default:documented=0;
 endcase end
endfunction
"""


def contract(opcodes):
    # SYNC replaces IR before dispatch. Testing the old IR in that branch
    # would incorrectly admit a newly fetched undocumented opcode.
    return (
        """module DocumentedModelContract(output equivalent,output context);
(* anyseq *) reg [255:0] state_in;
(* anyseq *) reg [63:0] pins_in;
wire [255:0] fullState,reducedState;
wire [63:0] fullPins,reducedPins;
wire fullTrap,reducedTrap;
Full sourceFull(.state_in(state_in),.pins_in(pins_in),.state_out(fullState),.pins_out(fullPins),.trap(fullTrap));
Reduced sourceReduced(.state_in(state_in),.pins_in(pins_in),.state_out(reducedState),.pins_out(reducedPins),.trap(reducedTrap));
"""
        + context_function(opcodes)
        + """
assign context=(pins_in[24] && pins_in[28]) ||
 (pins_in[25] ? documented(pins_in[23:16]) :
 (state_in[255:251]==0 && documented(state_in[250:243])));
assign equivalent=!context || (fullState==reducedState && fullPins==reducedPins && fullTrap==reducedTrap);
endmodule
"""
    )


def qualify():
    premises = [
        ROOT / "build/bbc" / n / "result.json"
        for n in ("cpu-c-structural-pruning", "cpu-c-structural-adapter")
    ]
    full_bound, adapter = [memory_composition.sealed(p) for p in premises]
    full = Path("/tmp/fpgaleet-c-structural-pruning/pruned.v")
    source = ROOT / "build/bbc/reference-cycle/m6502.h"
    if full_bound["artifacts"].get(str(full)) != digest(full) or adapter["inputs"].get(
        str(source.relative_to(ROOT))
    ) != digest(source):
        raise ValueError("Changed complete source model/header")
    values = inventory()
    work = Path("/tmp/fpgaleet-c-documented-model")
    work.mkdir(exist_ok=True)
    result = ROOT / "build/bbc/cpu-c-documented-model/result.json"
    result.parent.mkdir(exist_ok=True)
    files = [
        Path(__file__),
        *[
            Path(m.__file__)
            for m in (frontend, prepared, prune, short_stream, structural, memory_composition)
        ],
        Path(__file__).parents[1] / "video_cursor.py",
        *premises,
        full,
        source,
        ROOT / "tools/bbc/reference/official_stimulus.h",
        Path(shutil.which("gcc")),
        Path(shutil.which("yosys")),
        *sorted(Path(pycparser.__file__).parent.rglob("*.py")),
    ]
    before = hashes(files)
    expanded = subprocess.check_output(
        ["gcc", "-E", "-P", "-"], input=frontend.extract(source), text=True
    )
    model = work / "source.v"
    model.write_text(
        prune.prune(structural.generate(short_stream.adapter_ast(expanded, values)))[0]
    )
    property_file = work / "contract.v"
    property_file.write_text(contract(values))
    graph, mapped, log = [work / n for n in ("graph.json", "mapped.json", "proof.log")]
    preparation = f"read_verilog -formal -sv {full}; rename M6502ReferenceNext Full; read_verilog -formal -sv {model}; rename M6502ReferenceNext Reduced; read_verilog -formal {property_file}; prep -top DocumentedModelContract -flatten; check -assert; opt; memory_map; opt; write_json {graph}; techmap; opt; abc -g AND; opt_clean; write_json {mapped}"
    cached = prepared.prepare(
        work, preparation, [full, model, property_file], [graph, mapped], timeout=1800
    )
    for path in (graph, mapped):
        if any(
            c["type"] in ("$assume", "$assert")
            for c in json.loads(path.read_text())["modules"]["DocumentedModelContract"][
                "cells"
            ].values()
        ):
            raise ValueError("Unexpected assumption/assertion in context implication")
    with log.open("w") as output:
        positive = subprocess.run(
            [
                "yosys",
                "-Q",
                "-T",
                "-p",
                f"read_json {mapped}; sat -prove equivalent 1 -verify -timeout 600 -show context",
            ],
            stdout=output,
            stderr=subprocess.STDOUT,
            timeout=1200,
        )
    passed = positive.returncode == 0 and "SUCCESS!" in log.read_text()
    stable = before == hashes(files)
    write_json(
        result,
        {
            "passed": passed and stable,
            "inputs_unchanged": stable,
            "inputs": before,
            "artifacts": hashes([p for p in work.iterdir() if p.is_file()]),
            "preparation_cache_reused": cached,
            "opcodes": values,
            "original_bytes": full.stat().st_size,
            "reduced_bytes": model.stat().st_size,
            "scope": "Universal full-vs-reduced source-model equality of all 321 output bits over all 320 input bits whenever RDY holds a read, SYNC fetches a documented opcode, or non-SYNC IR names a documented opcode. All 151 official opcode cases, common pre/post-dispatch code and invalid microstep traps remain. Context is the antecedent of the proved implication; there are no assumptions or hidden-state overrides.",
            "limits": "Decoder preservation in the stated context, not new proof of C translation or game reachability. A history using this model must separately prove every executing input context is admitted. Undocumented opcodes, actual OS/clock/reset/device histories and whole-game composition remain separate.",
            "universal_game_equivalence": False,
        },
    )
    print(
        "Documented decoder preservation: " + ("PASS" if passed and stable else "FAIL"), flush=True
    )
    if not passed or not stable:
        raise ValueError("Documented decoder preservation incomplete")


if __name__ == "__main__":
    qualify()
