"""Extend read-only source interrupt relations to taken/page-crossing branches."""

import json
import shutil
import subprocess
from pathlib import Path

from tools.bbc.proofs import branch_induction, memory_composition
from tools.bbc.proofs.cpu_reference import instructions, prepared, short_irq
from tools.bbc.proofs.video_cursor import hashes, replace_once
from tools.validation.common import ROOT, digest, write_json


def strengthen(original):
    text = short_irq.strengthen(original)
    text = replace_once(text, "wire observedEquivalent;", "(* keep *) wire observedEquivalent;")
    c = instructions.fields("cState")
    for name in ("actualOpcode", "executingOpcode", "fetchedOpcode"):
        text = replace_once(
            text,
            f"twoCycle({name}) || {name}==0",
            f"twoCycle({name}) || branch({name}) || {name}==0",
        )
    text = replace_once(
        text,
        "stage==0 || stage==1 || (stage>=20 && stage<=24)",
        "stage<=3 || (stage>=20 && stage<=24)",
    )
    text = replace_once(
        text,
        f"{c['PC']}==(stage==0 ? currentPc : stage==1 ? ({c['brk_flags']}!=0 ? currentPc : nextInstructionPc) : returnPc)",
        f"{c['PC']}==(stage==0 ? currentPc : stage==1 ? ({c['brk_flags']}!=0 ? currentPc : nextInstructionPc) : stage<=3 ? fallPc : returnPc)",
    )
    text = replace_once(
        text,
        f"(stage==0 || {c['IR']}==(stage==1 ? (({{8'b0,decodedOpcode}}<<3)|16'd1) : {{11'b0,stage}}-16'd18))",
        f"(stage==0 || {c['IR']}==(stage==1 ? (({{8'b0,decodedOpcode}}<<3)|16'd1) : stage<=3 ? (({{8'b0,actualOpcode}}<<3)|{{11'b0,stage}}) : {{11'b0,stage}}-16'd18))",
    )
    # A same-page taken branch holds the sampled pipelines for one transition.
    # A page-crossing branch restores its first IRQ poll into the later poll.
    held = "branch(executingOpcode) && takenBranch && !crossedBranch"
    branched = "branch(executingOpcode) && takenBranch"
    text = replace_once(
        text,
        "field_irq_pip[8:0]==0 && field_nmi_pip[8:0]==0",
        f"field_irq_pip[8:0]==0 && field_nmi_pip[7:0]==0 && ((stage==0 && {held}) || !field_nmi_pip[8])",
    )
    text = replace_once(
        text,
        "field_irq_pip[9]==irqSample",
        f"field_irq_pip[9]==((stage==0 && {held}) ? irqSecond : irqSample)",
    )
    text = replace_once(
        text,
        "stage==24 || field_irq_pip[10]==irqSecond",
        f"stage==24 || (stage==1 && {held}) || field_irq_pip[10]==((stage==0 && {branched}) ? (crossedBranch ? (branchIrq || irqSecond) : branchIrq) : irqSecond)",
    )
    text = replace_once(
        text,
        "stage>=22 || nmiSecond==",
        f"stage>=22 || (stage==1 && {held}) || ((stage==0 && {held}) ? branchNmi : nmiSecond)==",
    )
    # These are conclusions about executing source state, never assumptions or
    # state assignments. The initial preceding branch latch remains arbitrary.
    extra = (
        f"(step==26 || field_branch_irq==(stage==1 ? irqSample : branchIrq)) &&\n"
        f"((stage!=2 && stage!=3) || (branch(actualOpcode) && takenBranch && {c['AD']}==targetPc)) &&\n"
        "(stage!=3 || crossedBranch) &&\n"
    )
    return replace_once(
        text, "(* keep *) wire stateRelation=", "(* keep *) wire stateRelation=" + extra
    )


def qualify():
    short_premise = ROOT / "build/bbc/cpu-c-short-irq-investigation/result.json"
    bridge_premise = ROOT / "build/bbc/cpu-c-short-irq-bridge/result.json"
    model_premise = ROOT / "build/bbc/cpu-c-quiet-stream-bridge/result.json"
    short, _, model_bound = [
        memory_composition.sealed(p) for p in (short_premise, bridge_premise, model_premise)
    ]
    model = Path("/tmp/fpgaleet-c-quiet-stream/positive/source.v")
    prefix = Path("/tmp/fpgaleet-c-protocol-invariants/prefix.v")
    if model_bound["artifacts"].get(str(model)) != digest(model) or short["inputs"].get(
        str(prefix)
    ) != digest(prefix):
        raise ValueError("Changed sealed branch source model/prefix")
    original = ROOT / "sim/formal/BbcCpuInterruptStreamContract.v"
    architecture = ROOT / "sim/formal/BbcCpuArchitectureContract.v"
    work = Path("/tmp/fpgaleet-c-branch-interrupts")
    work.mkdir(exist_ok=True)
    directory = ROOT / "build/bbc/cpu-c-branch-interrupts-investigation"
    directory.mkdir(exist_ok=True)
    files = [
        Path(__file__),
        Path(short_irq.__file__),
        Path(instructions.__file__),
        Path(prepared.__file__),
        Path(branch_induction.__file__),
        Path(memory_composition.__file__),
        Path(__file__).parents[1] / "video_cursor.py",
        short_premise,
        bridge_premise,
        model_premise,
        model,
        prefix,
        original,
        architecture,
        Path(shutil.which("yosys")),
    ]
    before = hashes(files)
    contract = work / "contract.v"
    contract.write_text(strengthen(original.read_text()))
    log, graph, mapped, witness = [
        work / n for n in ("proof.log", "graph.json", "mapped.json", "witness.json")
    ]
    witness.unlink(missing_ok=True)
    top = "BbcCpuInterruptStreamContract"
    preparation = (
        f"read_verilog -formal -sv {model}; read_verilog -formal {prefix} {contract} {architecture}; "
        f"prep -top {top} -flatten; check -assert; opt; chformal -lower; write_json {graph}; "
        f"techmap; opt; abc -g AND; opt_clean; write_json {mapped}"
    )
    cached = prepared.prepare(
        work, preparation, [model, prefix, contract, architecture], [graph, mapped]
    )
    query = (
        f"read_json {mapped}; sat -set-assumes -set-init-zero -tempinduct -initsteps 2 -maxsteps 14 "
        f"-prove equivalent 1 -verify -timeout 180 -show equivalent -show observedEquivalent -show stateRelation "
        f"-show stage -show executingOpcode -show takenBranch -show crossedBranch -show cState -show cHistory -show cPins -dump_json {witness}"
    )
    with log.open("w") as output:
        run = subprocess.run(
            ["yosys", "-Q", "-T", "-p", query],
            stdout=output,
            stderr=subprocess.STDOUT,
            timeout=1800,
        )
    branch_induction.guard_domains(json.loads(graph.read_text()), top)
    branch_induction.guard_domains(json.loads(mapped.read_text()), top)
    stable = before == hashes(files)
    passed = stable and run.returncode == 0 and "Induction step proven: SUCCESS!" in log.read_text()
    write_json(
        directory / "result.json",
        {
            "passed": passed,
            "inputs_unchanged": stable,
            "inputs": before,
            "artifacts": hashes([p for p in work.iterdir() if p.is_file()]),
            "preparation_cache_reused": cached,
            "scope": "Investigative initialized source history induction for 33 two-cycle instructions, all eight branches and BRK with arbitrary IRQ/NMI and response bytes. Read-only executing-source PC/IR/address and branch polling relations strengthen the unchanged bus/architecture property. Sole input assumption is its original 42-opcode domain.",
            "limits": "Requires successful induction, opcode admission and actual source faults before acceptance, then complete-decoder/cold-entry composition. Memory/control successors, C translation trust, actual waits/enables/warm reset and whole-game equivalence remain open.",
            "universal_game_equivalence": False,
        },
    )
    print("C branch interrupt history: " + ("PASS" if passed else "FAIL"), flush=True)
    if not passed:
        raise ValueError("Branch source history incomplete; inspect retained logs and witness")


if __name__ == "__main__":
    qualify()
