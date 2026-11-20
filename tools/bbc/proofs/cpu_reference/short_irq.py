"""Investigate source two-cycle/BRK streams with unrestricted IRQ and NMI."""

import json
import re
import shutil
import subprocess
from pathlib import Path

from tools.bbc.proofs import branch_induction, memory_composition
from tools.bbc.proofs.cpu_reference import instructions, prepared, protocol_invariants
from tools.bbc.proofs.video_cursor import hashes, replace_once
from tools.validation.common import ROOT, digest, write_json


def strengthen(original):
    text = protocol_invariants.strengthen(original)
    c, h = instructions.fields("cState"), instructions.fields("cHistory")
    for n, current, following in (
        ("A", "modelA", "nextA"),
        ("X", "modelX", "nextX"),
        ("Y", "modelY", "nextY"),
    ):
        text = replace_once(
            text,
            f"({c[n]}=={n.lower()} && (step==26 || {h[n]}=={n.lower()}))",
            f"({c[n]}==(primed ? {following} : {current}) && (step==26 || {h[n]}==((stage==1 && primed) ? {following} : {current})))",
        )
    text = replace_once(
        text, "(modelA==a && modelX==x && modelY==y && (modelP & 8'hcb)==(p & 8'hcb)) &&\n", ""
    )
    text = replace_once(
        text,
        "((actualOpcode==0 || actualOpcode==8'hea) && (executingOpcode==0 || executingOpcode==8'hea) && (fetchedOpcode==0 || fetchedOpcode==8'hea))",
        "((twoCycle(actualOpcode) || actualOpcode==0) && (twoCycle(executingOpcode) || executingOpcode==0) && (twoCycle(fetchedOpcode) || fetchedOpcode==0))",
    )
    text = replace_once(
        text,
        f"{c['PC']}==(stage==0 ? currentPc : stage==1 ? ({c['brk_flags']}!=0 ? currentPc : currentPc+16'd1) : returnPc)",
        f"{c['PC']}==(stage==0 ? currentPc : stage==1 ? ({c['brk_flags']}!=0 ? currentPc : nextInstructionPc) : returnPc)",
    )
    text = replace_once(
        text,
        f"(step==26 || {h['S']}==(stage==20 ? modelS : stage==21 ? modelS-8'd1 : stage==22 ? modelS-8'd2 : stage>=23 ? modelS-8'd3 : primed ? nextS : modelS))",
        f"(step==26 || {h['S']}==(stage==20 ? modelS : stage==21 ? modelS-8'd1 : stage==22 ? modelS-8'd2 : stage>=23 ? modelS-8'd3 : (stage==1 || (stage==0 && executingOpcode==0)) && primed ? nextS : modelS))",
    )
    text = replace_once(
        text,
        f"(({c['P']} & 8'hcb)==(p & 8'hcb) && (step==26 || ({h['P']} & 8'hcb)==(p & 8'hcb)))",
        f"(({c['P']} & 8'hcb)==((primed ? nextP : modelP) & 8'hcb) && (step==26 || ({h['P']} & 8'hcb)==(((stage==1 && primed) ? nextP : modelP) & 8'hcb)))",
    )
    text = replace_once(
        text,
        f"(step==26 || ({h['P']} & 8'h04)==(stage==24 ? 8'h04 : stage>=20 ? (modelP & 8'h04) : primed ? (nextP & 8'h04) : (modelP & 8'h04)))",
        f"(step==26 || ({h['P']} & 8'h04)==(stage==24 ? 8'h04 : stage>=20 ? (modelP & 8'h04) : (stage==1 || (stage==0 && executingOpcode==0)) && primed ? (nextP & 8'h04) : (modelP & 8'h04)))",
    )
    return text


def qualify():
    history_premise = ROOT / "build/bbc/cpu-c-protocol-history/result.json"
    history = memory_composition.sealed(history_premise)
    stream_premise = ROOT / "build/bbc/cpu-c-quiet-stream-bridge/result.json"
    stream = memory_composition.sealed(stream_premise)
    model = Path("/tmp/fpgaleet-c-quiet-stream/positive/source.v")
    prefix = Path("/tmp/fpgaleet-c-protocol-invariants/prefix.v")
    for path, bound in ((model, stream), (prefix, history)):
        if bound["artifacts"].get(str(path)) != digest(path):
            raise ValueError("Changed bound short interrupt model")
    original = ROOT / "sim/formal/BbcCpuInterruptStreamContract.v"
    architecture = ROOT / "sim/formal/BbcCpuArchitectureContract.v"
    work = Path("/tmp/fpgaleet-c-short-irq")
    work.mkdir(exist_ok=True)
    directory = ROOT / "build/bbc/cpu-c-short-irq-investigation"
    directory.mkdir(exist_ok=True)
    files = [
        Path(__file__),
        Path(instructions.__file__),
        Path(protocol_invariants.__file__),
        Path(prepared.__file__),
        Path(branch_induction.__file__),
        Path(memory_composition.__file__),
        Path(__file__).parents[1] / "video_cursor.py",
        history_premise,
        stream_premise,
        model,
        prefix,
        original,
        architecture,
        Path(shutil.which("yosys")),
    ]
    before = hashes(files)
    body = original.read_text()
    domains = re.findall(r"^always @\* assume\([^\n]+\);$", body, re.M)
    if len(domains) != 1:
        raise ValueError("Changed single opcode domain")
    body = replace_once(
        body,
        domains[0],
        "always @* assume((twoCycle(firstOpcode) || firstOpcode==0) && (twoCycle(nextOpcode) || nextOpcode==0));",
    )
    contract = work / "contract.v"
    contract.write_text(strengthen(body))
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
        f"-prove equivalent 1 -verify -timeout 180 -show equivalent -show stateRelation "
        f"-show stage -show cState -show cHistory -show cPins -dump_json {witness}"
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
            "scope": "Investigative initialized source history induction of 33 two-cycle operations plus BRK, arbitrary IRQ/NMI and response/vector bytes. Unchanged interrupt bus/architecture checks; read-only source relations generalized to arithmetic, register, flag and stack updates. Sole assumption restricts the two independent opcode slots; executing source state is retained.",
            "limits": "Investigation until induction, admission, actual source faults and complete decoder/cold-entry composition pass. Branch/memory/control successors, custom C translation, physical enables/warm reset and whole-machine/game equivalence remain open.",
            "universal_game_equivalence": False,
        },
    )
    print("C two-cycle interrupt history: " + ("PASS" if passed else "FAIL"), flush=True)
    if not passed:
        raise ValueError(
            "Source two-cycle interrupt history incomplete; inspect retained logs/witness"
        )


if __name__ == "__main__":
    qualify()
