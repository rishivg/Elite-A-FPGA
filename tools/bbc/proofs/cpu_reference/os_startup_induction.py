"""Extend source cold-Z normalization to arbitrarily many startup restarts."""

import json
import re
from pathlib import Path

from tools.bbc.proofs import memory_composition
from tools.bbc.proofs.cpu_reference import prepared, smt
from tools.bbc.proofs.video_cursor import hashes, replace_once
from tools.validation.common import ROOT, digest, write_json

INVARIANT = """
reg fresh=1;
always @(posedge clk)fresh<=0;
wire [15:0] ir=actualState[255:240],pc=actualState[239:224],ad=actualState[223:208];
wire [7:0] stack=actualState[183:176];
wire brk=ir>=1 && ir<=7;
wire lda=ir==16'h549;
wire coreShape=actualState[207:184]==0 && actualState[169] && !normalizedState[169] &&
 (actualState[175:168]&8'he9)==0 && actualState[167:104]==actualPins &&
 normalizedState[167:104]==normalizedPins && actualState[103:80]==0 &&
 actualState[79:48]==0 && actualState[39:32]==1 && actualState[31:0]==0 &&
 actualPins[29:26]==0 && actualPins[63:31]==0;
wire resetShape=brk && actualState[47:40]==(ir<=4 ? 8'd4:8'd0) &&
 actualState[172]==(ir>=5) && (ir<5 || actualState[170]) &&
 (ir==7 ? actualPins[25]:!actualPins[25]) &&
 (ir==1 ? (pc==0 || pc==16'hd9cd) && actualPins[15:0]==pc :
  ir<=4 ? (pc==1 || pc==16'hd9ce) && actualPins[15:0]=={8'h01,stack+8'd1} && (ir!=4 || ad==16'hfffc) :
  ir==5 ? (pc==1 || pc==16'hd9ce) && actualPins[15:0]==16'hfffc && ad==16'hfffd :
  ir==6 ? (pc==1 || pc==16'hd9ce) && actualPins[15:0]==16'hfffd && ad==16'hcd :
  pc==16'hd9cd && actualPins[15:0]==pc && ad==16'hcd);
wire ldaShape=lda && actualState[47:40]==0 && actualState[172] && actualState[170] &&
 !actualPins[25] && actualPins[15:0]==16'hd9ce && pc==16'hd9cf && ad==16'hcd;
wire startupInvariant=fresh ? actualState==256'hINITIAL_STATE &&
 normalizedState==256'hNORMALIZED_STATE && actualPins==64'h43000000 && normalizedPins==actualPins :
 coreShape && (resetShape || ldaShape);
assign equivalent=profileEquivalent && (joined || startupInvariant);
"""


def strengthen(text):
    values = re.findall(r"reg \[255:0\] (?:actualState|normalizedState)=256'h([0-9a-f]+);", text)
    if len(values) != 2:
        raise ValueError("Changed source startup initial profiles")
    text = replace_once(text, "assign equivalent=samePins", "wire profileEquivalent=samePins")
    invariant = INVARIANT.replace("INITIAL_STATE", values[0]).replace("NORMALIZED_STATE", values[1])
    return replace_once(text, "endmodule", invariant + "endmodule")


def qualify():
    parent = ROOT / "build/bbc/cpu-c-os-startup-profile/result.json"
    report = memory_composition.sealed(parent)
    previous = Path("/tmp/fpgaleet-c-source-os-startup-profile/positive")
    model, original = previous / "source.v", previous / "contract.v"
    for p in (model, original):
        if report["artifacts"].get(str(p)) != digest(p):
            raise ValueError("Changed bounded source startup premise")
    inputs = [
        Path(__file__),
        Path(smt.__file__),
        Path(prepared.__file__),
        Path(memory_composition.__file__),
        Path(__file__).parents[1] / "video_cursor.py",
        parent,
        model,
        original,
        *smt.tools(),
    ]
    before = hashes(inputs)
    work = Path("/tmp/fpgaleet-c-source-os-startup-induction")
    work.mkdir(exist_ok=True)
    contract, graph = work / "contract.v", work / "graph.json"
    contract.write_text(strengthen(original.read_text()))
    command = f"read_verilog -formal -sv {model} {contract}; prep -top SourceOsStartupProfile -flatten; check -assert; opt; memory_map; opt; write_json {graph}"
    cached = prepared.prepare(work, command, [model, contract], [graph], timeout=600)
    module = json.loads(graph.read_text())["modules"]["SourceOsStartupProfile"]
    if any(c["type"] in ("$assume", "$check") for c in module["cells"].values()):
        raise ValueError("Source startup induction acquired an assumption")
    proof, artifacts = smt.prove(work / "smt", graph, "SourceOsStartupProfile", steps=15)
    memory_composition.sealed(parent)
    stable = before == hashes(inputs)
    passed = stable and proof["passed"]
    write_json(
        ROOT / "build/bbc/cpu-c-os-startup-induction/result.json",
        {
            "passed": passed,
            "inputs_unchanged": stable,
            "inputs": before,
            "artifacts": hashes(
                [contract, graph, work / "prepared.json", work / "prepare.log", *artifacts]
            ),
            "proof": proof,
            "preparation_cache_reused": cached,
            "scope": "Initialized unbounded accepted-tick source startup-profile normalization with arbitrary warm requests and discarded read bytes, quiet IRQ/NMI, verified OS vector and first LDA #$40. Read-only reset/LDA state-shape invariants are conclusions; there are no assumptions. Complete 256-bit state and 64-bit pins converge on first LDA retirement. A sealed parent supplies decoder preservation, actual native initialization and the actual-C Z-update fault control.",
            "limits": "Execution freezes at convergence; retirement is conditional and can be postponed indefinitely by restarts. Physical enable timing and actual RTL/C, parent and whole-game composition remain separate.",
            "universal_game_equivalence": False,
        },
    )
    print("Source OS startup induction: " + ("PASS" if passed else "FAIL"), flush=True)
    if not passed:
        raise ValueError("Source OS startup induction incomplete")


if __name__ == "__main__":
    qualify()
