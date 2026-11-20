"""Check all official C instruction histories with physical NMI event summaries."""

import json
import shutil
import subprocess
from pathlib import Path

from tools.bbc.proofs import branch_induction, memory_composition
from tools.bbc.proofs.cpu_reference import (
    fetch_seed_domain,
    history_recipe,
    macro_mixed_rtl,
    nmi_sampling,
    prepared,
    source_history,
)
from tools.bbc.proofs.video_cursor import hashes, replace_once
from tools.validation.common import ROOT, write_json

DIRECTORY = ROOT / "build/bbc/cpu-source-event-histories"


def recipe(initial, pins):
    prefix, contract = history_recipe.generate(initial, pins)
    prefix = replace_once(
        prefix, "input clk, input seedNmi,", "input clk, input offNmiPin,riseNmiPin, input seedNmi,"
    )
    prefix = replace_once(
        prefix,
        "M6502ReferenceNext source(.state_in(referenceState)",
        """wire [31:0] eventPipeline=referenceState[79:48] |
    ((age>=26 && ALLOW_NMI && (offNmiPin || riseNmiPin)) ? 32'h100:32'd0);
  wire [63:0] eventPins=(referenceState[167:104]&~64'h8000000) |
    ({63'd0,age>=26 && ALLOW_NMI && nmiPin}<<27);
  wire [255:0] eventState={referenceState[255:168],eventPins,
    referenceState[103:80],eventPipeline,referenceState[47:0]};
  M6502ReferenceNext source(.state_in(eventState)""",
    )
    contract = replace_once(
        contract, "(* anyseq *) reg nmiPin;", "(* anyseq *) reg nmiPin,offNmiPin,riseNmiPin;"
    )
    contract = replace_once(
        contract,
        "wire nmiRise=liveNmi && !nmiPrevious;",
        "wire nmiRise=ALLOW_NMI && step>=26 && riseNmiPin;\nwire availableNmi=nmiPending || (ALLOW_NMI && step>=26 && offNmiPin);",
    )
    contract = replace_once(
        contract, "nmiVector<=nmiPending||nmiRise", "nmiVector<=availableNmi||nmiRise"
    )
    contract = replace_once(
        contract,
        "else if(nmiRise)nmiPending<=1;",
        "else if(nmiRise || (ALLOW_NMI && step>=26 && offNmiPin))nmiPending<=1;",
    )
    contract = replace_once(
        contract, "nmiSample<=nmiPending||nmiRise;", "nmiSample<=availableNmi||nmiRise;"
    )
    contract = replace_once(
        contract,
        ".clk(clk),.seedNmi(seedNmi)",
        ".clk(clk),.offNmiPin(offNmiPin),.riseNmiPin(riseNmiPin),.seedNmi(seedNmi)",
    )
    return prefix, contract


def qualify():
    parents = [
        source_history.DIRECTORY / "result.json",
        nmi_sampling.DIRECTORY / "result.json",
        macro_mixed_rtl.DIRECTORY / "result.json",
    ]
    reports = [memory_composition.sealed(path) for path in parents]
    if reports[0].get("opcodes") != list(range(256)) and len(reports[0].get("opcodes", [])) != 151:
        raise ValueError("Incomplete official instruction domain")
    DIRECTORY.mkdir(parents=True, exist_ok=True)
    model = source_history.DIRECTORY / "source.v"
    architecture = ROOT / "sim/formal/BbcCpuArchitectureContract.v"
    files = [
        Path(__file__),
        *parents,
        model,
        architecture,
        *history_recipe.producer_files(),
        Path(branch_induction.__file__),
        Path(memory_composition.__file__),
        Path(fetch_seed_domain.__file__),
        Path(prepared.__file__),
        Path(nmi_sampling.__file__),
        Path(macro_mixed_rtl.__file__),
        Path(__file__).parents[1] / "video_cursor.py",
        ROOT / "tools/validation/common.py",
        ROOT / "sim/formal/BbcCpuStreamPrefix.v",
        ROOT / "sim/formal/BbcCpuInterruptStreamContract.v",
        Path(shutil.which("yosys")),
    ]
    before = hashes(files)
    foundation = memory_composition.sealed(ROOT / "build/bbc/cpu-source-foundation/result.json")
    prefix, contract = recipe(foundation["default_native_state"], foundation["default_native_pins"])
    prefix_path, contract_path = DIRECTORY / "prefix.v", DIRECTORY / "contract.v"
    prefix_path.write_text(prefix)
    contract_path.write_text(contract)
    graph, mapped = DIRECTORY / "graph.json", DIRECTORY / "mapped.json"
    top = source_history.TOP
    prepared.prepare(
        DIRECTORY,
        f"read_verilog -formal -sv {model}; read_verilog -formal {prefix_path} {contract_path} {architecture}; prep -top {top} -flatten; check -assert; opt; memory_map; opt; chformal -lower; write_json {graph}; techmap; opt; abc -g AND; opt_clean; write_json {mapped}",
        [model, prefix_path, contract_path, architecture],
        [graph, mapped],
        timeout=1800,
    )
    for path in (graph, mapped):
        branch_induction.guard_domains(json.loads(path.read_text()), top)
    seeds = fetch_seed_domain.guard_seeds(json.loads(graph.read_text()))
    log, witness = DIRECTORY / "proof.log", DIRECTORY / "witness.json"
    witness.unlink(missing_ok=True)
    with log.open("w") as output:
        trial = subprocess.run(
            [
                "yosys",
                "-Q",
                "-T",
                "-p",
                f"read_json {mapped}; sat -set-assumes -set-init-zero -tempinduct -initsteps 14 -maxsteps 24 -prove equivalent 1 -verify -timeout 600 -show equivalent -show observedEquivalent -show stateRelation -show offNmiPin -show riseNmiPin -dump_json {witness}",
            ],
            stdout=output,
            stderr=subprocess.STDOUT,
            timeout=14400,
        )
    for path in parents:
        memory_composition.sealed(path)
    stable = before == hashes(files)
    passed = (
        stable and trial.returncode == 0 and "Induction step proven: SUCCESS!" in log.read_text()
    )
    write_json(
        DIRECTORY / "result.json",
        {
            "passed": passed,
            "inputs_unchanged": stable,
            "inputs": before,
            "artifacts": hashes(
                [
                    p
                    for p in (
                        prefix_path,
                        contract_path,
                        graph,
                        mapped,
                        log,
                        DIRECTORY / "prepare.log",
                        DIRECTORY / "prepared.json",
                        witness,
                    )
                    if p.is_file()
                ]
            ),
            "seeds": seeds,
            "opcodes": reports[0]["opcodes"],
            "scope": "Initialized unbounded source/independent-architecture history over all 151 official instructions, arbitrary IRQ/data and unrestricted wait/fresh NMI event summaries. The original complete-source decoder is retained. Event projection changes only its physical NMI latch/previous level; accepted pipeline age remains in the original tick. Generalized entry seeds and original opcode assumptions remain unchanged.",
            "limits": "Source-side event history. Physical reference adapter callback ordering, actual RTL relation, cold/warm handoff and whole-machine/game composition remain separate obligations.",
            "universal_game_equivalence": False,
        },
    )
    if not passed:
        raise ValueError("Source event history incomplete; retain investigation evidence")


if __name__ == "__main__":
    qualify()
