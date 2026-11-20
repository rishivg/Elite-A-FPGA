"""Investigate inductive C state relations for NOP/BRK IRQ/NMI streams."""

import json
import shutil
import subprocess
from pathlib import Path

from tools.bbc.proofs import branch_induction, memory_composition
from tools.bbc.proofs.cpu_reference import instructions, interrupt_stream, prepared
from tools.bbc.proofs.video_cursor import hashes, replace_once
from tools.validation.common import ROOT, write_json


def strengthen(original):
    c, h = instructions.fields("cState"), instructions.fields("cHistory")
    expressions = [
        f"{c[n]}=={n.lower()} && (step==26 || {h[n]}=={n.lower()})" for n in ("A", "X", "Y")
    ]
    expressions += [
        "modelA==a && modelX==x && modelY==y && (modelP & 8'hcb)==(p & 8'hcb)",
        "(actualOpcode==0 || actualOpcode==8'hea) && (executingOpcode==0 || executingOpcode==8'hea) && (fetchedOpcode==0 || fetchedOpcode==8'hea)",
        "stage==0 || stage==1 || (stage>=20 && stage<=24)",
        "cPins[63:28]==0",
        "cPins[25]==(stage==0)",
        "cPins[24]==!(stage>=20 && stage<=22)",
        "cPreviousFetch==(stage==1)",
        f"{c['PC']}==(stage==0 ? currentPc : stage==1 ? ({c['brk_flags']}!=0 ? currentPc : currentPc+16'd1) : returnPc)",
        f"(stage==0 || {c['IR']}==(stage==1 ? (({{8'b0,decodedOpcode}}<<3)|16'd1) : {{11'b0,stage}}-16'd18))",
        f"{c['S']}==(stage==20 ? modelS-8'd1 : stage==21 ? modelS-8'd2 : stage>=22 ? modelS-8'd3 : primed ? nextS : modelS)",
        f"(step==26 || {h['S']}==(stage==20 ? modelS : stage==21 ? modelS-8'd1 : stage==22 ? modelS-8'd2 : stage>=23 ? modelS-8'd3 : primed ? nextS : modelS))",
        f"({c['P']} & 8'hcb)==(p & 8'hcb) && (step==26 || ({h['P']} & 8'hcb)==(p & 8'hcb))",
        f"({c['P']} & 8'h04)==(stage>=23 ? 8'h04 : stage>=20 ? (modelP & 8'h04) : primed ? (nextP & 8'h04) : (modelP & 8'h04))",
        f"(step==26 || ({h['P']} & 8'h04)==(stage==24 ? 8'h04 : stage>=20 ? (modelP & 8'h04) : primed ? (nextP & 8'h04) : (modelP & 8'h04)))",
        f"(stage!=23 || {c['AD']}==(nmiVector ? 16'hfffb : 16'hffff))",
        f"(stage!=22 || {c['AD']}==(nmiVector ? 16'hfffa : 16'hfffe))",
        f"(stage!=24 || {c['AD']}=={{8'b0,pointerLow}})",
        f"{c['PINS']}[27]==nmiPrevious",
        f"(step==26 || {c['PINS']}==cPins)",
        f"{c['irq_pip']}[8:0]==0 && {c['nmi_pip']}[8:0]==0",
        f"{c['irq_pip']}[9]==irqSample",
        f"(stage==24 || {c['irq_pip']}[10]==irqSecond)",
        f"nmiPending==(({c['nmi_pip']}!=0) || ({c['brk_flags']}[1] && (stage==1 || stage==20 || stage==21)))",
        f"(stage>=22 || nmiSecond==(({c['nmi_pip']} & 32'hfffffc00)!=0 || ({c['brk_flags']}[1] && (stage==1 || stage==20 || stage==21))))",
        f"(stage!=1 || {c['brk_flags']}[1:0]=={{nmiPolled,irqPolled}})",
        f"({c['brk_flags']} & 8'hfc)==0",
        f"({c['P']} & 8'h30)==(stage==1 ? ({c['brk_flags']}!=0 ? 8'h00 : 8'h10) : stage>=20 && stage<=22 ? (hardwareInterrupt ? 8'h00 : 8'h10) : 8'h10)",
        f"{c['bcd_enabled']}==1",
        *[f"{c[n]}==0" for n in ("io_ddr", "io_out", "io_inp", "io_pins")],
    ]
    # Parenthesized vector slices are emitted as explicit field wires, keeping
    # ordinary Verilog parsing independent of expression-select extensions.
    aliases = "\n".join(
        f"wire [{width - 1}:0] field_{n}={c[n]};" for n, width in instructions.frontend.FIELDS
    )
    relation = " &&\n".join("(" + expression + ")" for expression in expressions)
    for n, _ in instructions.frontend.FIELDS:
        relation = relation.replace(c[n] + "[", "field_" + n + "[")
    original = replace_once(
        original,
        "BbcCpuInterruptStreamPrefix #(.ALLOW_NMI(ALLOW_NMI)) prefix(",
        "wire [255:0] cState,cHistory;wire [63:0] cPins;wire cPreviousFetch;\nBbcCpuInterruptStreamPrefix #(.ALLOW_NMI(ALLOW_NMI)) prefix(",
    )
    original = replace_once(
        original,
        ".flags(flags),.retiredFlags(retiredFlags));",
        ".flags(flags),.retiredFlags(retiredFlags),.proofState(cState),.proofPins(cPins),.proofHistory(cHistory),.proofPreviousFetch(cPreviousFetch));",
    )
    original = replace_once(
        original,
        "assign equivalent=reset ||",
        "wire observedEquivalent;\nassign observedEquivalent=reset ||",
    )
    return replace_once(
        original,
        "endmodule",
        aliases
        + "\n(* keep *) wire stateRelation="
        + relation
        + ";\nassign equivalent=observedEquivalent && (reset || stateRelation);\nendmodule",
    )


def qualify():
    cold = ROOT / "build/bbc/cpu-c-cold-entry-bridge/result.json"
    initialized = memory_composition.sealed(cold)
    stream_premise = ROOT / "build/bbc/cpu-c-quiet-stream-bridge/result.json"
    stream = memory_composition.sealed(stream_premise)
    model = Path("/tmp/fpgaleet-c-quiet-stream/positive/source.v")
    if stream["artifacts"].get(str(model)) != hashes([model])[str(model)]:
        raise ValueError("Changed sealed source model")
    ordinary = ROOT / "sim/formal/BbcCpuStreamPrefix.v"
    source_contract = Path("/tmp/fpgaleet-c-interrupt-stream-protocol/contract.v")
    architecture = ROOT / "sim/formal/BbcCpuArchitectureContract.v"
    original_contract = ROOT / "sim/formal/BbcCpuInterruptStreamContract.v"
    work = Path("/tmp/fpgaleet-c-protocol-invariants")
    work.mkdir(exist_ok=True)
    directory = ROOT / "build/bbc/cpu-c-protocol-invariants-investigation"
    directory.mkdir(exist_ok=True)
    files = [
        Path(__file__),
        Path(instructions.__file__),
        Path(interrupt_stream.__file__),
        Path(prepared.__file__),
        Path(branch_induction.__file__),
        Path(memory_composition.__file__),
        Path(__file__).parents[1] / "video_cursor.py",
        cold,
        stream_premise,
        model,
        ordinary,
        source_contract,
        original_contract,
        architecture,
        Path(shutil.which("yosys")),
    ]
    before = hashes(files)
    # Validate the sole exploratory opcode-domain change independently of any
    # result or prepared graph from the bounded protocol investigation.
    base = original_contract.read_text()
    import re

    domains = re.findall(r"^always @\* assume\([^\n]+\);$", base, re.M)
    if len(domains) != 1 or source_contract.read_text() != replace_once(
        base,
        domains[0],
        "always @* assume((firstOpcode==0 || firstOpcode==8'hea) && (nextOpcode==0 || nextOpcode==8'hea));",
    ):
        raise ValueError("Changed protocol property beyond its opcode domain")
    prefix = work / "prefix.v"
    text = interrupt_stream.prefix(
        initialized["default_native_state"],
        initialized["default_native_pins"],
        ordinary.read_text(),
    )
    text = replace_once(
        text,
        "output [255:0] proofState, output [63:0] proofPins, output proofTrap",
        "output [255:0] proofState, output [63:0] proofPins, output proofTrap, output [255:0] proofHistory, output proofPreviousFetch",
    )
    prefix.write_text(
        replace_once(
            text,
            "endmodule",
            "assign proofHistory=retirementState;assign proofPreviousFetch=previousFetch;\nendmodule",
        )
    )
    contract = work / "contract.v"
    contract.write_text(strengthen(source_contract.read_text()))
    log, graph, mapped, witness = [
        work / n for n in ("proof.log", "graph.json", "mapped.json", "witness.json")
    ]
    witness.unlink(missing_ok=True)
    top = "BbcCpuInterruptStreamContract"
    command = (
        f"read_verilog -formal -sv {model}; read_verilog -formal {prefix} {contract} {architecture}; "
        f"prep -top {top} -flatten; check -assert; opt; chformal -lower; write_json {graph}; "
        f"techmap; opt; abc -g AND; opt_clean; write_json {mapped}"
    )
    cached = prepared.prepare(
        work, command, [model, prefix, contract, architecture], [graph, mapped]
    )
    query = (
        f"read_json {mapped}; sat -set-assumes -set-init-zero -tempinduct -initsteps 2 -maxsteps 12 "
        f"-prove equivalent 1 -verify -timeout 180 -show equivalent -show stateRelation -show stage "
        f"-show cState -show cHistory -show cPins -dump_json {witness}"
    )
    with log.open("w") as output:
        run = subprocess.run(
            ["yosys", "-Q", "-T", "-p", query],
            stdout=output,
            stderr=subprocess.STDOUT,
            timeout=1200,
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
            "scope": "Investigative NOP/BRK C source history induction, strengthening the unchanged interrupt bus/architecture property with read-only source architectural, decoder, stack, pipeline and pending-NMI relations. Sole assumption is two independent NOP/BRK opcode slots; IRQ/NMI and vector bytes remain free. Every executing source state driver is retained.",
            "limits": "Investigation until initialized induction, admission, source faults, decoder/cold-entry composition and broader instruction streams pass. Custom C translation, actual clocks/enables, warm reset and universal whole-game equivalence remain separate.",
            "universal_game_equivalence": False,
        },
    )
    print("C interrupt protocol relation: " + ("PASS" if passed else "FAIL"), flush=True)
    if not passed:
        raise ValueError("Protocol relation investigation incomplete; inspect retained witness")


if __name__ == "__main__":
    qualify()
