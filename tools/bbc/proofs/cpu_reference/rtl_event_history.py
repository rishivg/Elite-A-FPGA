"""Check the actual RTL macro against the same source-side bus specification."""

import json
import shutil
import subprocess
from pathlib import Path

from tools.bbc.proofs import branch_induction, memory_composition, nmi_macro
from tools.bbc.proofs.cpu_reference import event_history, foundation, history_recipe, prepared
from tools.bbc.proofs.video_cursor import hashes, replace_once
from tools.validation.common import ROOT, write_json

DIRECTORY = ROOT / "build/bbc/cpu-rtl-event-histories"


def recipe(initial, pins):
    _, contract = event_history.recipe(initial, pins)
    # Use precisely the source theorem's bus/retirement observation. Its C
    # storage relation is an induction aid, and belongs to the C theorem.
    marker = "wire [15:0] field_IR=cState[255:240];"
    if contract.count(marker) != 1:
        raise ValueError("Changed source observation/storage boundary")
    contract = (
        contract[: contract.index(marker)] + "assign equivalent=observedEquivalent;\nendmodule\n"
    )
    contract = replace_once(
        contract, "(* anyconst *) reg seedNmi,seedPrevious;", "wire seedNmi=0,seedPrevious=0;"
    )
    contract = replace_once(
        contract, "(* anyconst *) reg [4:0] seedOlderIrq;", "wire [4:0] seedOlderIrq=0;"
    )
    contract = replace_once(
        contract, ".seedNmi(seedNmi),.seedPrevious(seedPrevious),.seedOlderIrq(seedOlderIrq),", ""
    )
    contract = replace_once(
        contract,
        ",.proofResponse(cResponse),.proofState(cState),.proofPins(cPins),.proofHistory(cHistory),.proofPreviousFetch(cPreviousFetch)",
        "",
    )
    prefix = (ROOT / "sim/formal/BbcCpuStreamPrefix.v").read_text()
    prefix = replace_once(
        prefix,
        "module BbcCpuStreamPrefix(",
        "module BbcCpuInterruptStreamPrefix #(parameter ALLOW_NMI=1)(",
    )
    prefix = replace_once(
        prefix, "input clk,", "input clk, input offNmiPin,riseNmiPin,irqPin,nmiPin,"
    )
    prefix = replace_once(
        prefix,
        ".irq(1'b0), .nmi(1'b0)",
        ".irq(age>=26 && irqPin), .nmi(age>=26 && ALLOW_NMI && nmiPin),\n    .offEvt(age>=26 && ALLOW_NMI && offNmiPin),.riseEvt(age>=26 && ALLOW_NMI && riseNmiPin)",
    )
    return prefix, contract


def qualify():
    parents = [
        foundation.DIRECTORY / "result.json",
        ROOT / "build/bbc/cpu-nmi-macro-transition-proof/result.json",
        ROOT / "build/bbc/cpu-nmi-idle-summary/result.json",
    ]
    reports = [memory_composition.sealed(path) for path in parents]
    DIRECTORY.mkdir(parents=True, exist_ok=True)
    core = ROOT / "build/bbc/ArletCpuEnabled.v"
    wrapper = ROOT / "rtl/bbc/BbcCpu.v"
    alu = ROOT / "third_party/6502/ALU.v"
    ordinary = ROOT / "sim/formal/BbcCpuStreamPrefix.v"
    architecture = ROOT / "sim/formal/BbcCpuArchitectureContract.v"
    files = [
        Path(__file__),
        Path(event_history.__file__),
        Path(nmi_macro.__file__),
        Path(prepared.__file__),
        Path(branch_induction.__file__),
        Path(memory_composition.__file__),
        *history_recipe.producer_files(),
        Path(__file__).parents[1] / "video_cursor.py",
        ROOT / "tools/validation/common.py",
        core,
        wrapper,
        alu,
        ordinary,
        architecture,
        ROOT / "sim/formal/BbcCpuInterruptStreamContract.v",
        *parents,
        Path(shutil.which("yosys")),
    ]
    before = hashes(files)
    prefix, contract = recipe(reports[0]["default_native_state"], reports[0]["default_native_pins"])
    macro_core, macro_wrapper = nmi_macro.transform(core.read_text(), wrapper.read_text())
    producer = ROOT / "build/bbc/cpu-nmi-macro-transition-proof"
    if (producer / "macro-core.v").read_text() != macro_core or (
        producer / "macro-wrapper.v"
    ).read_text() != macro_wrapper:
        raise ValueError("Changed accepted-edge RTL transformation")
    actual = DIRECTORY / "actual.v"
    actual.write_text(macro_core + "\n" + macro_wrapper + "\n" + alu.read_text())
    prefix_path, contract_path = DIRECTORY / "prefix.v", DIRECTORY / "contract.v"
    prefix_path.write_text(prefix)
    contract_path.write_text(contract)
    graph, mapped = DIRECTORY / "graph.json", DIRECTORY / "mapped.json"
    top = "BbcCpuInterruptStreamContract"
    prepared.prepare(
        DIRECTORY,
        f"read_verilog -formal -sv {actual} {prefix_path} {contract_path} {architecture}; prep -top {top} -flatten; check -assert; async2sync; opt; memory_map; opt; chformal -lower; write_json {graph}; techmap; opt; abc -g AND; opt_clean; write_json {mapped}",
        [actual, prefix_path, contract_path, architecture],
        [graph, mapped],
    )
    for path in (graph, mapped):
        branch_induction.guard_domains(json.loads(path.read_text()), top)
    log, witness = DIRECTORY / "proof.log", DIRECTORY / "witness.json"
    witness.unlink(missing_ok=True)
    with log.open("w") as output:
        trial = subprocess.run(
            [
                "yosys",
                "-Q",
                "-T",
                "-p",
                f"read_json {mapped}; sat -set-assumes -set-init-zero -tempinduct -initsteps 34 -maxsteps 96 -prove equivalent 1 -verify -timeout 240 -show equivalent -show stage -show step -dump_json {witness}",
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
                        actual,
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
            "scope": "Actual all-168-bit-proved RTL event macro against the exact source event theorem's bus/retirement observation, with real reset and register setup, zero entry interrupt seeds, all official opcode assumptions and arbitrary IRQ/NMI events/data.",
            "limits": "An initialized RTL macro history. Source-side theorem, physical event composition, actual cold/warm handoff, parent/device/video timing and whole-game equivalence remain separate obligations.",
            "universal_game_equivalence": False,
        },
    )
    if not passed:
        raise ValueError("RTL event history incomplete; retain investigation evidence")


if __name__ == "__main__":
    qualify()
