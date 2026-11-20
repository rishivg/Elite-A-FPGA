"""Investigate complete-source zero-page reads, stores and RMW interrupt histories."""

import json
import re
import shutil
import subprocess
from pathlib import Path

from tools.bbc.proofs import branch_induction, memory_composition
from tools.bbc.proofs.cpu_reference import branch_interrupts, instructions, prepared
from tools.bbc.proofs.video_cursor import hashes, replace_once
from tools.validation.common import ROOT, digest, write_json


def strengthen(original):
    domains = re.findall(r"^always @\* assume\([^\n]+\);$", original, re.M)
    if len(domains) != 1:
        raise ValueError("Changed single opcode domain")
    original = replace_once(
        original,
        domains[0],
        "always @* assume((twoCycle(firstOpcode)||branch(firstOpcode)||zeroPage(firstOpcode)||firstOpcode==0)&&(twoCycle(nextOpcode)||branch(nextOpcode)||zeroPage(nextOpcode)||nextOpcode==0));",
    )
    text = branch_interrupts.strengthen(original)
    c, h = instructions.fields("cState"), instructions.fields("cHistory")
    for n in ("actualOpcode", "executingOpcode", "fetchedOpcode"):
        text = replace_once(
            text,
            f"branch({n}) || {n}==0",
            f"branch({n}) || zeroPage({n}) || {n}==0"
            + (f" || {n}==8'h81" if n == "executingOpcode" else ""),
        )
    text = replace_once(
        text,
        "stage<=3 || (stage>=20 && stage<=24)",
        "stage<=4 || stage==11 || stage==12 || (stage>=20 && stage<=24)",
    )
    text = replace_once(
        text,
        "cPins[24]==!(stage>=20 && stage<=22)",
        "cPins[24]==!((stage>=20 && stage<=22) || stage==11 || stage==12 || (stage==4 && (executingOpcode==8'h81 || executingOpcode==8'h84 || executingOpcode==8'h86)))",
    )
    text = replace_once(
        text,
        "stage<=3 ? fallPc : returnPc",
        "stage<=3 ? fallPc : (stage==4 || stage==11 || stage==12) ? currentPc : returnPc",
    )
    text = replace_once(
        text,
        "stage<=3 ? (({8'b0,actualOpcode}<<3)|{11'b0,stage}) : {11'b0,stage}-16'd18",
        "stage<=3 ? (({8'b0,actualOpcode}<<3)|{11'b0,stage}) : (stage==4 || stage==11 || stage==12) ? (({8'b0,actualOpcode}<<3)|(stage==4 ? 16'd2 : stage==11 ? 16'd3 : 16'd4)) : {11'b0,stage}-16'd18",
    )
    for name, model, following in (
        ("A", "modelA", "nextA"),
        ("X", "modelX", "nextX"),
        ("Y", "modelY", "nextY"),
    ):
        text = replace_once(
            text,
            f"{c[name]}==(primed ? {following} : {model})",
            f"{c[name]}==((primed && stage<=1) ? {following} : {model})",
        )
    text = replace_once(
        text,
        f"({c['P']} & 8'hcb)==((primed ? nextP : modelP) & 8'hcb)",
        f"({c['P']} & 8'hcb)==(((primed && (stage<=1 || stage==12)) ? nextP : modelP) & 8'hcb)",
    )
    text = replace_once(
        text,
        f"({h['P']} & 8'hcb)==(((stage==1 && primed) ? nextP : modelP) & 8'hcb)",
        f"({h['P']} & 8'hcb)==((((stage==1 || (stage==0 && executingRmw)) && primed) ? nextP : modelP) & 8'hcb)",
    )
    # The C engine retains the original read byte in AD during its old-byte
    # write. INC/DEC then update the complete 16-bit temporary before truncating
    # the new write byte. These are observed transitions, not state assignments.
    extra = (
        "(!primed || executingOpcode==semantic(actualOpcode)) &&\n"
        "(!primed || executingRmw==rmw(actualOpcode)) &&\n"
        "(!primed || executingIncDec==(semantic(actualOpcode)==8'hca || semantic(actualOpcode)==8'he8)) &&\n"
        "(stage!=4 || zeroPage(actualOpcode)) &&\n"
        "((stage!=11 && stage!=12) || (zeroPage(actualOpcode) && rmw(actualOpcode))) &&\n"
        "(stage<20 || actualOpcode==0) &&\n"
        f"(stage!=11 || (executingRmw && {c['AD']}=={{8'b0,executingOperand}})) &&\n"
        f"(stage!=12 || (executingRmw && {c['AD']}==(executingOpcode==8'he8 ? {{8'b0,executingOperand}}+16'd1 : executingOpcode==8'hca ? {{8'b0,executingOperand}}-16'd1 : {{8'b0,executingOperand}}))) &&\n"
    )
    return replace_once(
        text, "(* keep *) wire stateRelation=", "(* keep *) wire stateRelation=" + extra
    )


def qualify():
    branch_premise = ROOT / "build/bbc/cpu-c-branch-interrupts-investigation/result.json"
    full_premise = ROOT / "build/bbc/cpu-c-structural-pruning/result.json"
    branch, full = [memory_composition.sealed(p) for p in (branch_premise, full_premise)]
    model = Path("/tmp/fpgaleet-c-structural-pruning/pruned.v")
    prefix = Path("/tmp/fpgaleet-c-protocol-invariants/prefix.v")
    if full["artifacts"].get(str(model)) != digest(model) or branch["inputs"].get(
        str(prefix)
    ) != digest(prefix):
        raise ValueError("Changed complete source model/prefix")
    original = ROOT / "sim/formal/BbcCpuInterruptStreamContract.v"
    architecture = ROOT / "sim/formal/BbcCpuArchitectureContract.v"
    work = Path("/tmp/fpgaleet-c-zero-page-interrupts")
    work.mkdir(exist_ok=True)
    directory = ROOT / "build/bbc/cpu-c-zero-page-interrupts-investigation"
    directory.mkdir(exist_ok=True)
    files = [
        Path(__file__),
        Path(branch_interrupts.__file__),
        Path(instructions.__file__),
        Path(prepared.__file__),
        Path(branch_induction.__file__),
        Path(memory_composition.__file__),
        Path(__file__).parents[1] / "video_cursor.py",
        branch_premise,
        full_premise,
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
        work, preparation, [model, prefix, contract, architecture], [graph, mapped], timeout=1800
    )
    query = (
        f"read_json {mapped}; sat -set-assumes -set-init-zero -tempinduct -initsteps 12 -maxsteps 16 "
        f"-prove equivalent 1 -verify -timeout 300 -show equivalent -show observedEquivalent -show stateRelation "
        f"-show stage -show actualOpcode -show executingOpcode -show executingOperand -show cState -show cHistory -show cPins -dump_json {witness}"
    )
    with log.open("w") as output:
        run = subprocess.run(
            ["yosys", "-Q", "-T", "-p", query],
            stdout=output,
            stderr=subprocess.STDOUT,
            timeout=3600,
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
            "scope": "Investigative initialized IRQ/NMI induction against the complete C decoder, adding all zero-page reads, stores and RMW instructions to the short/branch/BRK domain. Executing source state and original bus/architecture checks remain intact; read-only relations account for partial operations and old/new RMW writes. Sole assumption restricts opcode inputs.",
            "limits": "Unaccepted investigation until positive induction, opcode admission and actual source faults pass. Indexed/absolute/indirect memory and remaining control instructions, cold/OS entry composition, source translation trust, real enables/warm reset and whole-machine/game equivalence remain open.",
            "universal_game_equivalence": False,
        },
    )
    print("C zero-page interrupt history: " + ("PASS" if passed else "FAIL"), flush=True)
    if not passed:
        raise ValueError("Zero-page source history incomplete; inspect retained logs and witness")


if __name__ == "__main__":
    qualify()
