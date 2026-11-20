"""Investigate actual C restart requests through initialized mixed histories."""

import json
import shutil
import subprocess
from pathlib import Path

from tools.bbc.proofs import branch_induction, memory_composition
from tools.bbc.proofs.cpu_reference import prepared
from tools.bbc.proofs.video_cursor import hashes, replace_once
from tools.validation.common import ROOT, digest, write_json


def contract(text):
    text = replace_once(
        text,
        "reg [4:0] stage=0;",
        """reg [4:0] stage=0;
(* anyconst *) reg [4:0] warmAt;
reg [4:0] elapsed=0;
always @(posedge clk) if(elapsed!=31)elapsed<=elapsed+1;
wire warmRequest=!reset && elapsed==warmAt;
reg warmQueued=0,secondDummy=0,resetEntry=0;
reg [15:0] restartPc=0;
wire warmStart=stage==0 && !secondDummy && (warmQueued||warmRequest);
reg nmiRecent=0,nmiRecent2=0;
always @(posedge clk)begin
 if(reset)begin nmiRecent<=0;nmiRecent2<=0;end
 else begin
  nmiRecent<=(stage==24 && actualOpcode==0 && nmiPending)||nmiRise;
  nmiRecent2<=nmiRecent;
 end
end
always @(posedge clk)begin
 if(reset)begin warmQueued<=0;secondDummy<=0;end
 else begin
  if(warmRequest)warmQueued<=1;
  if(warmStart)begin warmQueued<=0;secondDummy<=1;restartPc<=currentPc;end
  else if(secondDummy)secondDummy<=0;
 end
end
""",
    )
    text = replace_once(
        text, ".clk(clk),.irqPin(irqPin)", ".clk(clk),.warmRequest(warmRequest),.irqPin(irqPin)"
    )
    text = replace_once(
        text,
        "case(stage)\n 1:begin",
        "case(stage)\n 25:begin expectedAddress=restartPc;returnedData=nextOperand;end\n 1:begin",
    )
    text = replace_once(
        text,
        "expectedAddress=nmiVector?16'hfffa:16'hfffe",
        "expectedAddress=resetEntry?16'hfffc:nmiVector?16'hfffa:16'hfffe",
    )
    text = replace_once(
        text,
        "expectedAddress=nmiVector?16'hfffb:16'hffff",
        "expectedAddress=resetEntry?16'hfffd:nmiVector?16'hfffb:16'hffff",
    )
    text = replace_once(
        text,
        "wire expectedWrite=stage==20 || stage==21 || stage==22",
        "wire expectedWrite=(!resetEntry && (stage==20 || stage==21 || stage==22))",
    )
    text = replace_once(
        text, "stage<=0;primed<=0;currentPc", "resetEntry<=0;stage<=0;primed<=0;currentPc"
    )
    text = replace_once(
        text,
        "?heldOpcode:nextOpcode;stage<=1;end",
        "?heldOpcode:nextOpcode;stage<=warmStart?25:1;end",
    )
    text = replace_once(
        text,
        "  1:begin\n   hardwareInterrupt",
        """  25:begin
   if(primed)begin modelA<=nextA;modelX<=nextX;modelY<=nextY;modelS<=nextS;modelP<=nextP;end
   primed<=1;committed<=1;actualOpcode<=0;executingOpcode<=0;executingOperand<=0;
   executingRmw<=0;executingIncDec<=0;hardwareInterrupt<=0;stage<=20;resetEntry<=1;
  end
  1:begin
   resetEntry<=0;hardwareInterrupt""",
    )
    text = replace_once(
        text, "if(stage==21 && step>=26)", "if(stage==21 && step>=26 && !resetEntry)"
    )
    # The C SYNC dispatch consumes older polled NMI into brk_flags even when
    # RES selects the reset vector, retaining only the recent pipeline bits.
    # The same rule is used by the independently proved RTL restart model.
    text = replace_once(
        text,
        "if(stage==21 && step>=26 && !resetEntry)",
        "if(warmStart)nmiPending<=nmiRecent||nmiRise||(branch(actualOpcode)&&takenBranch&&!crossedBranch&&nmiRecent2);\n  else if(stage==21 && step>=26 && !resetEntry)",
    )
    text = replace_once(
        text,
        "assign equivalent=observedEquivalent && (reset || stateRelation);",
        "assign equivalent=observedEquivalent && (reset || sourceContext);",
    )
    # This bounded diagnostic proves observations and defined source dispatch,
    # not the memory-only induction relation. Allow its unused cone to vanish.
    text = replace_once(text, "(* keep *) wire stateRelation=", "wire stateRelation=")
    return text


def qualify():
    premise = ROOT / "build/bbc/cpu-c-control-induction-investigation/result.json"
    parent = memory_composition.sealed(premise)
    directory = Path("/tmp/fpgaleet-c-control-induction")
    original_contract = directory / "contract.v"
    original_prefix = Path("/tmp/fpgaleet-c-documented-memory/prefix.v")
    model = Path("/tmp/fpgaleet-c-documented-model/source.v")
    if parent["artifacts"].get(str(original_contract)) != digest(original_contract) or parent[
        "inputs"
    ].get(str(original_prefix)) != digest(original_prefix):
        raise ValueError("Changed initialized source history")
    if parent["inputs"].get(str(model)) != digest(model):
        raise ValueError("Changed actual source decoder")
    work = Path("/tmp/fpgaleet-c-warm-histories")
    work.mkdir(exist_ok=True)
    architecture = ROOT / "sim/formal/BbcCpuArchitectureContract.v"
    inputs = [
        Path(__file__),
        Path(prepared.__file__),
        Path(branch_induction.__file__),
        Path(memory_composition.__file__),
        Path(__file__).parents[1] / "video_cursor.py",
        premise,
        original_contract,
        original_prefix,
        model,
        architecture,
        Path(shutil.which("yosys")),
    ]
    before = hashes(inputs)
    prefix = work / "prefix.v"
    text = replace_once(
        original_prefix.read_text(),
        "input clk, input irqPin",
        "input clk, input warmRequest, input irqPin",
    )
    # Match reference_cycle.restart(): retain RES until the actual C tick
    # clears it on its SYNC dispatch. The executing C state remains untouched.
    text = replace_once(
        text,
        "wire [63:0] response=",
        "reg requestPending=0;\n  always @(posedge clk) if(reset)requestPending<=0;else requestPending<=nextPins[30];\n  wire [63:0] response=",
    )
    response = next(line for line in text.splitlines() if "wire [63:0] response=" in line)
    updated = response[:-1] + " | ({63'd0,requestPending||warmRequest}<<30);"
    text = replace_once(text, response, updated)
    prefix.write_text(text)
    property_file = work / "contract.v"
    property_file.write_text(contract(original_contract.read_text()))
    graph, mapped, log, witness = [
        work / n for n in ("graph.json", "mapped.json", "proof.log", "witness.json")
    ]
    witness.unlink(missing_ok=True)
    command = f"read_verilog -formal -sv {model}; read_verilog -formal {prefix} {property_file} {architecture}; prep -top BbcCpuInterruptStreamContract -flatten; check -assert; opt; memory_map; opt; chformal -lower; write_json {graph}; techmap; opt; abc -g AND; opt_clean; write_json {mapped}"
    cached = prepared.prepare(
        work, command, [model, prefix, property_file, architecture], [graph, mapped], timeout=1800
    )
    for path in (graph, mapped):
        branch_induction.guard_domains(
            json.loads(path.read_text()), "BbcCpuInterruptStreamContract"
        )
    query = f"read_json {mapped}; sat -set-assumes -set-init-zero -seq 24 -set firstOpcode 234 -set nextOpcode 234 -prove equivalent 1 -verify -timeout 600 -show equivalent -show observedEquivalent -show sourceContext -show warmAt -show warmRequest -show stage -show resetEntry -show nmiPin -show irqPin -show cState -dump_json {witness}"
    with log.open("w") as output:
        run = subprocess.run(
            ["yosys", "-Q", "-T", "-p", query],
            stdout=output,
            stderr=subprocess.STDOUT,
            timeout=1800,
        )
    stable = before == hashes(inputs)
    passed = (
        stable
        and run.returncode == 0
        and "SAT proof finished - no model found: SUCCESS!" in log.read_text()
    )
    write_json(
        ROOT / "build/bbc/cpu-c-warm-histories-investigation/result.json",
        {
            "passed": passed,
            "inputs_unchanged": stable,
            "inputs": before,
            "artifacts": hashes([p for p in work.iterdir() if p.is_file()]),
            "preparation_cache_reused": cached,
            "scope": "Initialized 24-tick actual-C warm-request diagnostic. One freely timed restart request, NOP successors, arbitrary IRQ/NMI and response bytes. RES retained until the actual C engine clears it, matching reference_cycle.restart; unchanged source state transitions. Independent bus/architecture reset model and dispatch context checked.",
            "limits": "Bounded investigative diagnostic; no unbounded warm/enable/global equivalence or accepted fault controls.",
            "universal_game_equivalence": False,
        },
    )
    print("Source warm history: " + ("PASS" if passed else "FAIL"), flush=True)
    if not passed:
        raise ValueError("Source warm history investigation incomplete")


if __name__ == "__main__":
    qualify()
