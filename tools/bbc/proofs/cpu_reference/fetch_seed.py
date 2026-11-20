"""Extend official source histories to the interrupt state left by reset."""

import argparse
import json
import shutil
import subprocess
from pathlib import Path

from tools.bbc.proofs import branch_induction, memory_composition
from tools.bbc.proofs.cpu_reference import fetch_seed_domain, prepared, smt
from tools.bbc.proofs.video_cursor import hashes, replace_once
from tools.validation.common import ROOT, digest, write_json


def contract(text):
    text = replace_once(
        text,
        "reg [4:0] stage=0;",
        "(* anyconst *) reg seedNmi,seedPrevious;\n"
        "(* anyconst *) reg [4:0] seedOlderIrq;\nreg [4:0] stage=0;",
    )
    text = replace_once(
        text,
        ".clk(clk),.irqPin(irqPin)",
        ".clk(clk),.seedNmi(seedNmi),.seedPrevious(seedPrevious),.seedOlderIrq(seedOlderIrq),.irqPin(irqPin)",
    )
    text = replace_once(text, "nmiPrevious<=liveNmi;", "nmiPrevious<=reset ? seedPrevious:liveNmi;")
    text = replace_once(
        text, "if(reset)begin nmiPending<=0;", "if(reset)begin nmiPending<=seedNmi;"
    )
    return replace_once(
        text,
        "branchIrq<=0;nmiSample<=0;nmiSecond<=0;nmiPolled<=0;branchNmi<=0;end",
        "branchIrq<=0;nmiSample<=seedNmi;nmiSecond<=0;nmiPolled<=0;branchNmi<=0;end",
    )


def prefix(text):
    text = replace_once(
        text,
        "input clk, input irqPin",
        "input clk, input seedNmi, input seedPrevious, input [4:0] seedOlderIrq, input irqPin",
    )
    return replace_once(
        text,
        "({63'b0,ALLOW_NMI && nmiPin}<<27)),16'd0,hiddenBranch,32'd0,8'd0,8'd1",
        "({63'b0,seedPrevious}<<27)),{seedOlderIrq,11'd0},hiddenBranch,{22'd0,seedNmi,9'd0},8'd0,8'd1",
    )


def qualify(engine="sat"):
    if engine not in ("sat", "smt"):
        raise ValueError("Select the SAT or SMT source-history engine")
    premise = ROOT / "build/bbc/cpu-c-control-induction-investigation/result.json"
    history = memory_composition.sealed(premise)
    original_contract = Path("/tmp/fpgaleet-c-control-induction/contract.v")
    original_prefix = Path("/tmp/fpgaleet-c-documented-memory/prefix.v")
    model = Path("/tmp/fpgaleet-c-documented-model/source.v")
    for path, group in (
        (original_contract, "artifacts"),
        (original_prefix, "inputs"),
        (model, "inputs"),
    ):
        if history[group].get(str(path)) != digest(path):
            raise ValueError("Changed initialized official history premise")
    architecture = ROOT / "sim/formal/BbcCpuArchitectureContract.v"
    inputs = [
        Path(__file__),
        *[
            Path(m.__file__)
            for m in (branch_induction, memory_composition, prepared, smt, fetch_seed_domain)
        ],
        Path(__file__).parents[1] / "video_cursor.py",
        premise,
        original_contract,
        original_prefix,
        model,
        architecture,
        Path(shutil.which("yosys")),
    ]
    if engine == "smt":
        inputs += list(smt.tools())
    before = hashes(inputs)
    work = Path("/tmp/fpgaleet-c-fetch-seed")
    work.mkdir(exist_ok=True)
    property_file, prefix_file = work / "contract.v", work / "prefix.v"
    property_file.write_text(contract(original_contract.read_text()))
    prefix_file.write_text(prefix(original_prefix.read_text()))
    graph, mapped, log, witness = [
        work / n for n in ("graph.json", "mapped.json", "proof.log", "witness.json")
    ]
    command = f"read_verilog -formal -sv {model}; read_verilog -formal {prefix_file} {property_file} {architecture}; prep -top BbcCpuInterruptStreamContract -flatten; check -assert; opt; memory_map; opt; chformal -lower; write_json {graph}; techmap; opt; abc -g AND; opt_clean; write_json {mapped}"
    cached = prepared.prepare(
        work,
        command,
        [model, prefix_file, property_file, architecture],
        [graph, mapped],
        timeout=1800,
    )
    for path in (graph, mapped):
        branch_induction.guard_domains(
            json.loads(path.read_text()), "BbcCpuInterruptStreamContract"
        )
    seed_domain = fetch_seed_domain.guard_seeds(json.loads(graph.read_text()))
    witness.unlink(missing_ok=True)
    module = json.loads(mapped.read_text())["modules"]["BbcCpuInterruptStreamContract"]
    shows = " ".join(
        "-show " + name
        for name in (
            "equivalent",
            "observedEquivalent",
            "stateRelation",
            "seedNmi",
            "seedPrevious",
            "seedOlderIrq",
            "stage",
        )
        if name in module["netnames"]
    )
    query = f"read_json {mapped}; sat -set-assumes -set-init-zero -tempinduct -initsteps 14 -maxsteps 24 -prove equivalent 1 -verify -timeout 600 {shows} -dump_json {witness}"
    if engine == "smt":
        proof, smt_artifacts = smt.prove(work / "smt", graph, "BbcCpuInterruptStreamContract")
        accepted = proof["passed"]
        artifacts = smt_artifacts
    else:
        with log.open("w") as output:
            run = subprocess.run(
                ["yosys", "-Q", "-T", "-p", query],
                stdout=output,
                stderr=subprocess.STDOUT,
                timeout=7200,
            )
        accepted = run.returncode == 0 and "Induction step proven: SUCCESS!" in log.read_text()
        proof, smt_artifacts = {"passed": accepted}, []
        artifacts = [log, *([witness] if witness.exists() else [])]
    memory_composition.sealed(premise)
    stable = before == hashes(inputs)
    passed = stable and accepted
    write_json(
        ROOT / "build/bbc/cpu-c-fetch-seed-investigation/result.json",
        {
            "passed": passed,
            "inputs_unchanged": stable,
            "inputs": before,
            "artifacts": hashes(
                [
                    property_file,
                    prefix_file,
                    graph,
                    mapped,
                    work / "prepared.json",
                    work / "prepare.log",
                    *artifacts,
                ]
            ),
            "preparation_cache_reused": cached,
            "engine": engine,
            "proof": proof,
            "seed_domain": seed_domain,
            "scope": "Initialized all-151 official source instruction/IRQ/NMI history induction from a generalized abstract fetch entry. Arbitrary retained NMI bit at pipeline bit 9, previous NMI level and five older IRQ pipeline bits. Executing decoder and post-entry transitions unchanged; independent model and source entry share only those free initial parameters. Sole assumption remains the two official-opcode slots.",
            "limits": "Generalized abstract entry induction. Seed admission and observable entry fault controls have separate receipts. Actual reset retirement-model/live-pin handoff, source-to-RTL composition, real enable/wait scheduling, OS/reachability and full-game equivalence remain separate obligations.",
            "universal_game_equivalence": False,
        },
    )
    print("Generalized source fetch entry: " + ("PASS" if passed else "FAIL"), flush=True)
    if not passed:
        raise ValueError("Generalized source fetch history incomplete")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", choices=("sat", "smt"), default="sat")
    qualify(parser.parse_args().engine)
