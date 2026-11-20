"""Investigate mixed official memory/branch/BRK histories in the complete C CPU."""

import json
import re
import shutil
import subprocess
from pathlib import Path

from tools.bbc.proofs import branch_induction, memory_composition
from tools.bbc.proofs.cpu_reference import (
    branch_interrupts,
    instructions,
    prepared,
    zero_page_interrupts,
)
from tools.bbc.proofs.video_cursor import hashes, replace_once
from tools.validation.common import ROOT, digest, write_json

MEMORY = "zeroPage({n})||zeroX({n})||zeroY({n})||absolute({n})||absoluteX({n})||absoluteY({n})||indirectX({n})||indirectY({n})"


def strengthen(original):
    text = zero_page_interrupts.strengthen(original)
    for name in ("firstOpcode", "nextOpcode"):
        text = replace_once(text, f"zeroPage({name})", "(" + MEMORY.format(n=name) + ")")
    for name in ("actualOpcode", "executingOpcode", "fetchedOpcode"):
        text = replace_once(
            text,
            f"branch({name}) || zeroPage({name})",
            f"branch({name}) || (" + MEMORY.format(n=name) + ")",
        )
    c = instructions.fields("cState")
    text = replace_once(text, "stage<=4 || stage==11 || stage==12", "stage<=12")
    text = replace_once(
        text, "stage==4 && (executingOpcode", "(stage==4 || stage==6) && (executingOpcode"
    )
    text = replace_once(
        text,
        "nextInstructionPc) : stage<=3",
        "nextInstructionPc-((absolute(decodedOpcode)||absoluteX(decodedOpcode)||absoluteY(decodedOpcode))?16'd1:16'd0)) : stage<=3",
    )
    text = replace_once(
        text,
        "(stage==4 || stage==11 || stage==12) ? currentPc",
        "(stage>=4 && stage<=12) ? currentPc",
    )
    ir = (
        "stage==4 ? (zeroPage(actualOpcode)?16'd2:16'd3) : "
        "stage==5 ? 16'd2 : stage==6 ? (indirectX(actualOpcode)||indirectY(actualOpcode)?16'd5:absoluteX(actualOpcode)||absoluteY(actualOpcode)?16'd4:16'd3) : "
        "stage==7 ? (indirectY(actualOpcode)?16'd4:16'd3) : stage==8 ? 16'd2 : "
        "stage==9 ? (indirectX(actualOpcode)?16'd3:16'd2) : stage==10 ? (indirectX(actualOpcode)?16'd4:16'd3) : "
        "stage==11 ? (zeroPage(actualOpcode)?16'd3:absoluteX(actualOpcode)||absoluteY(actualOpcode)?16'd5:16'd4) : "
        "(zeroPage(actualOpcode)?16'd4:absoluteX(actualOpcode)||absoluteY(actualOpcode)?16'd6:16'd5)"
    )
    text = replace_once(
        text,
        "(stage==4 || stage==11 || stage==12) ? (({8'b0,actualOpcode}<<3)|(stage==4 ? 16'd2 : stage==11 ? 16'd3 : 16'd4))",
        "(stage>=4 && stage<=12) ? (({8'b0,actualOpcode}<<3)|(" + ir + "))",
    )
    text = replace_once(
        text,
        "(stage!=4 || zeroPage(actualOpcode))",
        "(stage!=4 || zeroPage(actualOpcode)||zeroX(actualOpcode)||zeroY(actualOpcode))",
    )
    text = replace_once(
        text,
        "(zeroPage(actualOpcode) && rmw(actualOpcode))",
        "((" + MEMORY.format(n="actualOpcode") + ") && rmw(actualOpcode))",
    )
    extra = (
        f"(stage!=5 || ((absolute(actualOpcode)||absoluteX(actualOpcode)||absoluteY(actualOpcode)) && {c['AD']}=={{8'd0,zeroAddress}})) &&\n"
        f"(stage!=6 || ((absolute(actualOpcode)||absoluteX(actualOpcode)||absoluteY(actualOpcode)||indirectX(actualOpcode)||indirectY(actualOpcode)) && {c['AD']}==(indirectX(actualOpcode)?{{8'd0,pointerLow}}:indirectY(actualOpcode)?{{pointerHigh,pointerLow}}:absoluteX(actualOpcode)||absoluteY(actualOpcode)?{{absoluteHigh,zeroAddress}}:{{8'd0,zeroAddress}}))) &&\n"
        f"(stage!=7 || ((absoluteX(actualOpcode)||absoluteY(actualOpcode)||indirectY(actualOpcode)) && {c['AD']}==(indirectY(actualOpcode)?{{pointerHigh,pointerLow}}:{{absoluteHigh,zeroAddress}}))) &&\n"
        f"(stage!=8 || ((zeroX(actualOpcode)||zeroY(actualOpcode)||indirectX(actualOpcode)) && {c['AD']}=={{8'd0,zeroAddress}})) &&\n"
        f"(stage!=9 || ((indirectX(actualOpcode)||indirectY(actualOpcode)) && {c['AD']}=={{8'd0,indirectX(actualOpcode)?indexedZero:zeroAddress}})) &&\n"
        f"(stage!=10 || ((indirectX(actualOpcode)||indirectY(actualOpcode)) && {c['AD']}=={{8'd0,pointerLow}})) &&\n"
        f"(stage!=4 || (!(zeroX(actualOpcode)||zeroY(actualOpcode)) || {c['AD']}=={{8'd0,zeroAddress}})) &&\n"
    )
    return replace_once(
        text, "(* keep *) wire stateRelation=", "(* keep *) wire stateRelation=" + extra
    )


def qualify():
    premises = [
        ROOT / "build/bbc" / n / "result.json"
        for n in ("cpu-c-branch-interrupts-investigation", "cpu-c-structural-pruning")
    ]
    branch, full = [memory_composition.sealed(p) for p in premises]
    model = Path("/tmp/fpgaleet-c-structural-pruning/pruned.v")
    prefix = Path("/tmp/fpgaleet-c-protocol-invariants/prefix.v")
    if full["artifacts"].get(str(model)) != digest(model) or branch["inputs"].get(
        str(prefix)
    ) != digest(prefix):
        raise ValueError("Changed complete decoder or interrupt prefix")
    original = ROOT / "sim/formal/BbcCpuInterruptStreamContract.v"
    architecture = ROOT / "sim/formal/BbcCpuArchitectureContract.v"
    work = Path("/tmp/fpgaleet-c-memory-interrupts")
    work.mkdir(exist_ok=True)
    destination = ROOT / "build/bbc/cpu-c-memory-interrupts-investigation/result.json"
    destination.parent.mkdir(exist_ok=True)
    files = [
        Path(__file__),
        *[
            Path(m.__file__)
            for m in (
                branch_interrupts,
                zero_page_interrupts,
                instructions,
                prepared,
                branch_induction,
                memory_composition,
            )
        ],
        Path(__file__).parents[1] / "video_cursor.py",
        *premises,
        model,
        prefix,
        original,
        architecture,
        Path(shutil.which("yosys")),
    ]
    before = hashes(files)
    contract = work / "contract.v"
    contract.write_text(strengthen(original.read_text()))
    domain = re.findall(r"^always @\* assume\([^\n]+\);$", contract.read_text(), re.M)
    if len(domain) != 1 or "control(" in domain[0]:
        raise ValueError("Unexpected memory-only input assumption")
    log, graph, mapped, witness = [
        work / n for n in ("proof.log", "graph.json", "mapped.json", "witness.json")
    ]
    witness.unlink(missing_ok=True)
    command = f"read_verilog -formal -sv {model}; read_verilog -formal {prefix} {contract} {architecture}; prep -top BbcCpuInterruptStreamContract -flatten; check -assert; opt; chformal -lower; write_json {graph}; techmap; opt; abc -g AND; opt_clean; write_json {mapped}"
    cached = prepared.prepare(
        work, command, [model, prefix, contract, architecture], [graph, mapped], timeout=1800
    )
    query = f"read_json {mapped}; sat -set-assumes -set-init-zero -tempinduct -initsteps 14 -maxsteps 20 -prove equivalent 1 -verify -timeout 300 -show equivalent -show observedEquivalent -show stateRelation -show stage -show actualOpcode -show executingOpcode -show cState -show cHistory -show cPins -dump_json {witness}"
    with log.open("w") as output:
        run = subprocess.run(
            ["yosys", "-Q", "-T", "-p", query],
            stdout=output,
            stderr=subprocess.STDOUT,
            timeout=5400,
        )
    for path in (graph, mapped):
        branch_induction.guard_domains(
            json.loads(path.read_text()), "BbcCpuInterruptStreamContract"
        )
    stable = before == hashes(files)
    passed = stable and run.returncode == 0 and "Induction step proven: SUCCESS!" in log.read_text()
    write_json(
        destination,
        {
            "passed": passed,
            "inputs_unchanged": stable,
            "inputs": before,
            "artifacts": hashes([p for p in work.iterdir() if p.is_file()]),
            "preparation_cache_reused": cached,
            "scope": "Investigative complete-C-decoder induction for mixed official memory instructions, short operations, all branches and BRK with arbitrary IRQ/NMI and response bytes. Executing source transitions and original bus/architecture property unchanged; only read-only relations extended. Sole assumption is the opcode input domain.",
            "limits": "Unaccepted until positive induction, admission and actual source faults pass. Nine other control instructions, cold/OS entry, actual enables/warm reset, peripheral scheduling and whole-game composition remain open.",
            "universal_game_equivalence": False,
        },
    )
    print("Complete source memory IRQ/NMI histories: " + ("PASS" if passed else "FAIL"), flush=True)
    if not passed:
        raise ValueError("Source memory interrupt investigation incomplete")


if __name__ == "__main__":
    qualify()
