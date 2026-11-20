"""Extend read-only source relations to official stack and jump histories."""

import json
import shutil
import subprocess
from pathlib import Path

from tools.bbc.proofs import branch_induction, memory_composition
from tools.bbc.proofs.cpu_reference import instructions, prepared
from tools.bbc.proofs.video_cursor import hashes, replace_once
from tools.validation.common import ROOT, digest, write_json


def relation(text, start, replacement):
    matches = [line for line in text.splitlines() if line.startswith(start)]
    if len(matches) != 1 or not matches[0].endswith(" &&"):
        raise ValueError("Changed read-only relation: " + start)
    return replace_once(text, matches[0], replacement + " &&")


def strengthen(text):
    assumption = next(line for line in text.splitlines() if line.startswith("always @* assume("))
    text = replace_once(
        text, assumption, "always @* assume(documented(firstOpcode)&&documented(nextOpcode));"
    )
    c, h = instructions.fields("cState"), instructions.fields("cHistory")
    wires = """
wire controlPhase=stage>=13 && stage<=19;
wire jumpOpcode=decodedOpcode==8'h4c || decodedOpcode==8'h6c || decodedOpcode==8'h20;
wire restoredPhase=actualOpcode==8'h40 && (stage==18 || stage==19);
wire [15:0] controlIr=(stage==13 ? (actualOpcode==8'h20 ? 16'd5:16'd2) :
 stage==14 ? 16'd3 : stage==15 ? 16'd4 : {11'd0,stage}-16'd14);
wire [15:0] controlPc=(stage==13 ? currentPc+(actualOpcode==8'h20 ? 16'd2:16'd3) :
 stage==14 || stage==15 ? currentPc+16'd3 :
 actualOpcode==8'h20 ? currentPc+16'd2 :
 stage==19 && actualOpcode==8'h60 ? {pointerHigh,pointerLow}+16'd1 :
 currentPc+((push(actualOpcode)||pull(actualOpcode)) ? 16'd0:16'd1));
wire [7:0] controlStack=(stage<=15 ? modelS-(actualOpcode==8'h20 ? 8'd2:8'd0) :
 push(actualOpcode) ? modelS-8'd1 : pull(actualOpcode) ? modelS+8'd1 :
 actualOpcode==8'h20 ? modelS-(stage==16 ? 8'd0:stage==17 ? 8'd1:8'd2) :
 actualOpcode==8'h40 ? modelS+(stage==16 ? 8'd1:stage==17 ? 8'd2:8'd3) :
 modelS+(stage==16 ? 8'd1:8'd2));
wire [7:0] controlHistoryStack=(stage<=15 ? modelS-(actualOpcode==8'h20 ? 8'd2:8'd0) :
 stage==16 ? modelS :
 actualOpcode==8'h20 ? modelS-(stage==17 ? 8'd0:8'd1) :
 stage==17 ? modelS+8'd1 : stage==18 ? modelS+8'd2 :
 modelS+(actualOpcode==8'h40 ? 8'd3:8'd2));
"""
    text = replace_once(
        text, "(* keep *) wire stateRelation=", wires + "(* keep *) wire stateRelation="
    )
    text = relation(
        text,
        "((twoCycle(actualOpcode)",
        "(documented(actualOpcode) && documented(executingOpcode) && documented(fetchedOpcode))",
    )
    text = relation(text, "(stage<=12 ||", "(stage<=24)")
    text = relation(text, "(cPins[24]==", "(cPins[24]==!expectedWrite)")
    # The initialized source advances PC on the operand request; stack/jump
    # temporary PCs and IR microsteps are observed separately from retirement.
    old = next(line for line in text.splitlines() if line.startswith(f"({c['PC']}=="))
    updated = replace_once(
        old, "nextInstructionPc-", "(jumpOpcode ? currentPc+16'd2 : nextInstructionPc)-"
    )
    updated = replace_once(updated, " : returnPc))", " : controlPhase ? controlPc : returnPc))")
    text = replace_once(text, old, updated)
    old = next(
        line for line in text.splitlines() if line.startswith("((stage==0 || cState[255:240]")
    )
    updated = replace_once(
        old,
        " : {11'b0,stage}-16'd18",
        " : controlPhase ? (({8'b0,actualOpcode}<<3)|controlIr) : {11'b0,stage}-16'd18",
    )
    text = replace_once(text, old, updated)
    old = next(line for line in text.splitlines() if line.startswith(f"({c['S']}=="))
    text = replace_once(
        text,
        old,
        replace_once(old, " : primed ? nextS", " : controlPhase ? controlStack : primed ? nextS"),
    )
    old = next(line for line in text.splitlines() if line.startswith(f"((step==26 || {h['S']}=="))
    updated = replace_once(
        old, " : (stage==1 ||", " : controlPhase ? controlHistoryStack : (stage==1 ||"
    )
    updated = replace_once(
        updated, "(stage==0 && executingOpcode==0)", "(stage==0 && control(executingOpcode))"
    )
    text = replace_once(text, old, updated)
    old = next(line for line in text.splitlines() if line.startswith(f"(({c['P']} & 8'hcb)=="))
    updated = replace_once(old, "stage<=1 || stage==12", "stage<=1 || stage==12 || restoredPhase")
    updated = replace_once(
        updated, "stage==0 && executingRmw", "stage==0 && (executingRmw || actualOpcode==8'h40)"
    )
    updated = replace_once(
        updated,
        "stage==1 || (stage==0",
        "stage==1 || (actualOpcode==8'h40 && stage==19) || (stage==0",
    )
    text = replace_once(text, old, updated)
    old = next(line for line in text.splitlines() if line.startswith(f"(({c['P']} & 8'h04)=="))
    updated = replace_once(old, " : primed ?", " : primed && (stage<=1 || restoredPhase) ?")
    text = replace_once(text, old, updated)
    old = next(
        line for line in text.splitlines() if line.startswith(f"((step==26 || ({h['P']} & 8'h04)==")
    )
    updated = replace_once(
        old, "(stage==1 ||", "(stage==1 || (actualOpcode==8'h40 && stage==19) ||"
    )
    updated = replace_once(
        updated, "executingOpcode==0", "(executingOpcode==0 || executingOpcode==8'h40)"
    )
    text = replace_once(text, old, updated)
    extra = (
        "(!controlPhase || control(actualOpcode)) &&\n"
        f"(stage!=13 || {c['AD']}=={{8'd0,zeroAddress}}) &&\n"
        f"(stage!=14 || (actualOpcode==8'h6c && {c['AD']}=={{absoluteHigh,zeroAddress}})) &&\n"
        f"(stage!=15 || (actualOpcode==8'h6c && {c['AD']}=={{8'd0,pointerLow}})) &&\n"
        f"(stage!=18 || actualOpcode!=8'h60 || {c['AD']}=={{8'd0,pointerLow}}) &&\n"
        f"(stage!=19 || (actualOpcode==8'h60 || actualOpcode==8'h40) && {c['AD']}=={{8'd0,pointerLow}}) &&\n"
    )
    return replace_once(
        text, "(* keep *) wire stateRelation=", "(* keep *) wire stateRelation=" + extra
    )


def qualify():
    premise = ROOT / "build/bbc/cpu-c-documented-memory-investigation/result.json"
    parent = memory_composition.sealed(premise)
    positive = Path("/tmp/fpgaleet-c-documented-memory")
    prefix, old_contract = positive / "prefix.v", positive / "contract.v"
    model = Path("/tmp/fpgaleet-c-documented-model/source.v")
    for path in (prefix, old_contract):
        if parent["artifacts"].get(str(path)) != digest(path):
            raise ValueError("Changed positive source history")
    if parent["inputs"].get(str(model)) != digest(model):
        raise ValueError("Changed preserved decoder")
    architecture = ROOT / "sim/formal/BbcCpuArchitectureContract.v"
    inputs = [
        Path(__file__),
        *[Path(m.__file__) for m in (instructions, prepared, branch_induction, memory_composition)],
        Path(__file__).parents[1] / "video_cursor.py",
        premise,
        prefix,
        old_contract,
        model,
        architecture,
        Path(shutil.which("yosys")),
    ]
    before = hashes(inputs)
    work = Path("/tmp/fpgaleet-c-control-induction")
    work.mkdir(exist_ok=True)
    contract = work / "contract.v"
    contract.write_text(strengthen(old_contract.read_text()))
    graph, mapped, log, witness = [
        work / n for n in ("graph.json", "mapped.json", "proof.log", "witness.json")
    ]
    witness.unlink(missing_ok=True)
    command = f"read_verilog -formal -sv {model}; read_verilog -formal {prefix} {contract} {architecture}; prep -top BbcCpuInterruptStreamContract -flatten; check -assert; opt; memory_map; opt; chformal -lower; write_json {graph}; techmap; opt; abc -g AND; opt_clean; write_json {mapped}"
    cached = prepared.prepare(
        work, command, [model, prefix, contract, architecture], [graph, mapped], timeout=1800
    )
    for path in (graph, mapped):
        branch_induction.guard_domains(
            json.loads(path.read_text()), "BbcCpuInterruptStreamContract"
        )
    query = f"read_json {mapped}; sat -set-assumes -set-init-zero -tempinduct -initsteps 14 -maxsteps 24 -prove equivalent 1 -verify -timeout 600 -show equivalent -show observedEquivalent -show sourceContext -show stateRelation -show stage -show actualOpcode -show fetchedOpcode -show cState -show cHistory -dump_json {witness}"
    with log.open("w") as output:
        run = subprocess.run(
            ["yosys", "-Q", "-T", "-p", query],
            stdout=output,
            stderr=subprocess.STDOUT,
            timeout=7200,
        )
    stable = before == hashes(inputs)
    passed = stable and run.returncode == 0 and "Induction step proven: SUCCESS!" in log.read_text()
    write_json(
        ROOT / "build/bbc/cpu-c-control-induction-investigation/result.json",
        {
            "passed": passed,
            "inputs_unchanged": stable,
            "inputs": before,
            "artifacts": hashes([p for p in work.iterdir() if p.is_file()]),
            "preparation_cache_reused": cached,
            "scope": "Investigative initialized 151-official-opcode source history induction with free IRQ/NMI and response bytes. Read-only stack/jump temporary relations added; source dispatch context remains a conclusion. Sole assumption is the two free opcode slots. Actual executing model, responses, bus and architecture drivers remain unchanged.",
            "limits": "Unaccepted until induction, full admission and actual C faults pass. Abstract quiet fetch entry, actual OS reachability, enables/warm resets and global scheduling remain open.",
            "universal_game_equivalence": False,
        },
    )
    print("All documented source histories: " + ("PASS" if passed else "FAIL"), flush=True)
    if not passed:
        raise ValueError("Source control induction investigation incomplete")


if __name__ == "__main__":
    qualify()
