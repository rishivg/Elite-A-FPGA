"""Run the all-instruction RTL event specification after real reset and LDA."""

import json
import re
import shutil
import subprocess
from pathlib import Path

from tools.bbc.proofs import branch_induction, memory_composition, nmi_macro
from tools.bbc.proofs.cpu_reference import (
    foundation,
    history_recipe,
    prepared,
    rtl_event_history,
)
from tools.bbc.proofs.video_cursor import hashes, replace_once
from tools.validation.common import ROOT, write_json

DIRECTORY = ROOT / "build/bbc/cpu-cold-lda-event-histories"


def recipe(initial, pins):
    prefix, contract = rtl_event_history.recipe(initial, pins)
    contract = replace_once(
        contract,
        "(* anyconst *) reg [7:0] a,x,y,s,p,firstOpcode;",
        "(* anyconst *) reg [7:0] firstOpcode;\nwire [7:0] a=8'h40,x=0,y=0,s=8'hfd,p=4;",
    )
    prefix = replace_once(
        prefix, "assign step = age;", "wire [5:0] coldAge=age+6'd17;\n  assign step=coldAge;"
    )
    if prefix.count("age>=26") != 4:
        raise ValueError("Changed actual prefix interrupt timing")
    prefix = prefix.replace("age>=26", "coldAge>=26")
    cases = re.findall(r"case \(age\)[\s\S]*?endcase", prefix)
    if len(cases) != 1:
        raise ValueError("Changed native cold prefix response table")
    prefix = replace_once(
        prefix,
        cases[0],
        """case(coldAge)
      17,18:begin requestedAddress=0;returnedData=8'hea;end
      19:begin requestedAddress=16'h0100;returnedData=8'hea;end
      20:begin requestedAddress=16'h01ff;returnedData=8'hea;end
      21:begin requestedAddress=16'h01fe;returnedData=8'hea;end
      22:begin requestedAddress=16'hfffc;returnedData=(base+16'd11)&16'hff;end
      23:begin requestedAddress=16'hfffd;returnedData=(base+16'd11)>>8;end
      24:begin requestedAddress=base+16'd11;returnedData=8'ha9;end
      25:begin requestedAddress=base+16'd12;returnedData=8'h40;end
    endcase""",
    )
    prefix = replace_once(
        prefix, "age < 26 ? age == 15 : targetWrite", "coldAge < 26 ? 1'b0 : targetWrite"
    )
    prefix = replace_once(prefix, "age < 26 ? p : targetValue", "coldAge < 26 ? p : targetValue")
    prefix = replace_once(prefix, "age != 28", "coldAge != 28")
    prefix = replace_once(prefix, "if (age < 30)", "if (age < 13)")
    prefix = replace_once(prefix, "if (age == retirementStep)", "if (coldAge == retirementStep)")
    return prefix, contract


def qualify():
    # The general RTL history supplies the recipe. This query independently
    # proves the specialized cold prefix and its continuation.
    parents = [
        foundation.DIRECTORY / "result.json",
        ROOT / "build/bbc/cpu-nmi-macro-transition-proof/result.json",
    ]
    reports = [memory_composition.sealed(path) for path in parents]
    DIRECTORY.mkdir(parents=True, exist_ok=True)
    core, wrapper, alu = [
        ROOT / name
        for name in ("build/bbc/ArletCpuEnabled.v", "rtl/bbc/BbcCpu.v", "third_party/6502/ALU.v")
    ]
    architecture = ROOT / "sim/formal/BbcCpuArchitectureContract.v"
    files = [
        Path(__file__),
        Path(rtl_event_history.__file__),
        Path(nmi_macro.__file__),
        Path(prepared.__file__),
        Path(branch_induction.__file__),
        Path(memory_composition.__file__),
        *history_recipe.producer_files(),
        ROOT / "tools/bbc/proofs/cpu_reference/event_history.py",
        ROOT / "tools/validation/common.py",
        Path(__file__).parents[1] / "video_cursor.py",
        ROOT / "sim/formal/BbcCpuStreamPrefix.v",
        ROOT / "sim/formal/BbcCpuInterruptStreamContract.v",
        architecture,
        core,
        wrapper,
        alu,
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
        raise ValueError("Changed proved RTL macro")
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
                f"read_json {mapped}; sat -set-assumes -set-init-zero -tempinduct -initsteps 24 -maxsteps 72 -prove equivalent 1 -verify -timeout 300 -show equivalent -show stage -show step -dump_json {witness}",
            ],
            stdout=output,
            stderr=subprocess.STDOUT,
            timeout=10800,
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
                        DIRECTORY / "prepared.json",
                        DIRECTORY / "prepare.log",
                        witness,
                    )
                    if p.is_file()
                ]
            ),
            "scope": "All official instructions and arbitrary IRQ/NMI event summaries/data after eight real reset edges, seven reset bus slots and LDA #$40. Actual RTL macro, fixed OS-entry registers and arbitrary reset vector; no register/stack/PLP initialization program. The post-LDA bus/retirement specification is identical to the generalized source event specification with its admitted concrete entry parameters.",
            "limits": "Initialized RTL macro history. Matching source event theorem and native LDA entry, physical event/enable composition, OS memory/interrupt profile, warm reset and whole-machine/game composition remain separate obligations.",
            "universal_game_equivalence": False,
        },
    )
    if not passed:
        raise ValueError("Cold LDA RTL event history incomplete; retain investigation evidence")


if __name__ == "__main__":
    qualify()
