"""Relate one or more cold-reset edges to the accepted CPU startup."""

import copy
import json
import shutil
import subprocess
from pathlib import Path

from tools.bbc.proofs import memory_composition
from tools.bbc.proofs.cpu_reference.reset_repetition import reset_cut
from tools.bbc.proofs.video_cursor import hashes
from tools.validation.common import ROOT, write_json

DIRECTORY = ROOT / "build/bbc/cpu-reset-release"
IDLE = ROOT / "build/bbc/cpu-nmi-idle-summary"
OBSERVATIONS = {
    "address": 16,
    "write_data": 8,
    "write_enable": 1,
    "instruction": 1,
    "sync": 1,
    "pc": 16,
}


def release_cuts(graph):
    reset, constructor, async_mask, async_values = reset_cut(graph)
    body = reset["modules"]["ResetOnlyTransition"]
    state = body["ports"]["state_in"]["bits"]
    transient = [
        state.index(bit)
        for name in ("core.backwards", "core.cond_code")
        for bit in body["netnames"][name]["bits"]
    ]
    if len(set(transient)) != 4:
        raise ValueError("Changed branch-state inventory")
    branch_mask = sum(1 << i for i in transient)
    active = copy.deepcopy(body)
    data = {
        q: d
        for cell in graph["modules"]["BbcCpu"]["cells"].values()
        if cell["type"] == "$adff"
        for q, d in zip(cell["connections"]["Q"], cell["connections"]["D"], strict=True)
    }
    follow = active["ports"]["following"]["bits"]
    for i, bit in enumerate(state):
        if bit in data:
            follow[i] = data[bit]
    initial = (constructor & ~async_mask) | async_values
    return (
        {"modules": {"ResetOnlyTransition": body, "ReleasedTransition": active}},
        initial,
        async_mask,
        async_values,
        branch_mask,
    )


def instance(module, name, *, reset, enable, irq, data, state, following, signals=""):
    return (
        f"{module} {name}(.clk(1'b0), .reset({reset}), .enable({enable}), .irq({irq}), "
        f".nmi(1'b0), .warm_reset(1'b0), .read_data({data}), "
        f".state_in({state}), .following({following}){signals});"
    )


def contract(initial, async_mask, async_values, branch_mask):
    lines = [
        "module ResetRelease(output resetMatches, releaseMatches, branchCanDiffer);",
        f"localparam [167:0] INITIAL=168'h{initial:042x};",
        f"localparam [167:0] ASYNC_MASK=168'h{async_mask:042x};",
        f"localparam [167:0] ASYNC_VALUES=168'h{async_values:042x};",
        f"localparam [167:0] BRANCH_MASK=168'h{branch_mask:042x};",
        "(* anyseq *) reg resetEnable, resetIrq;",
        "(* anyseq *) reg [7:0] resetData, data0, data1, data2;",
        "(* anyseq *) reg [167:0] branchState;",
        "wire [167:0] canonical, first, again;",
        "wire [167:0] held=(canonical&~BRANCH_MASK)|(branchState&BRANCH_MASK);",
        "wire [167:0] normalized=(held&~ASYNC_MASK)|ASYNC_VALUES;",
    ]
    for name, state, data, enable, irq in (
        ("canonical", "INITIAL", "8'hea", "1'b1", "1'b0"),
        ("first", "INITIAL", "resetData", "resetEnable", "resetIrq"),
        ("again", "normalized", "resetData", "resetEnable", "resetIrq"),
    ):
        lines.append(
            instance(
                "ResetOnlyTransition",
                f"r_{name}",
                reset="1'b1",
                enable=enable,
                irq=irq,
                data=data,
                state=state,
                following=name,
            )
        )
    lines.extend(
        [
            "assign resetMatches=((first^canonical)&~BRANCH_MASK)==0 && "
            "((again^canonical)&~BRANCH_MASK)==0;",
            "assign branchCanDiffer=first!=canonical || again!=canonical;",
            "wire [167:0] a0=canonical, b0=held;",
        ]
    )
    observations = []
    for step in range(3):
        for side in ("a", "b"):
            lines.append(f"wire [167:0] {side}{step + 1};")
            for signal, width in OBSERVATIONS.items():
                lines.append(f"wire [{width - 1}:0] {side}{step}_{signal};")
            signals = "".join(f", .{name}({side}{step}_{name})" for name in OBSERVATIONS)
            lines.append(
                instance(
                    "ReleasedTransition",
                    f"s_{side}{step}",
                    reset="1'b0",
                    enable="1'b1",
                    irq="1'b0",
                    data=f"data{step}",
                    state=f"{side}{step}",
                    following=f"{side}{step + 1}",
                    signals=signals,
                )
            )
        for signal in OBSERVATIONS:
            if signal == "write_data":
                observations.append(
                    f"(!a{step}_write_enable || a{step}_{signal}==b{step}_{signal})"
                )
            else:
                observations.append(f"a{step}_{signal}==b{step}_{signal}")
    lines.append("assign releaseMatches=(a3==b3) && " + " && ".join(observations) + ";")
    return "\n".join([*lines, "endmodule", ""])


def qualify():
    premise = IDLE / "result.json"
    receipt = memory_composition.sealed(premise)
    registers = IDLE / "positive/registers.json"
    files = [
        Path(__file__),
        Path(__file__).with_name("reset_repetition.py"),
        Path(memory_composition.__file__),
        premise,
        registers,
        Path(shutil.which("yosys")),
    ]
    before = hashes(files)
    if (
        receipt["artifacts"].get(str(registers.relative_to(ROOT)))
        != before[str(registers.relative_to(ROOT))]
    ):
        raise ValueError("Physical state cut is absent from the disabled-edge proof")
    DIRECTORY.mkdir(parents=True, exist_ok=True)
    graph, *parameters = release_cuts(json.loads(registers.read_text()))
    cut, prop, log, witness = [
        DIRECTORY / name for name in ("cut.json", "contract.v", "proof.log", "witness.json")
    ]
    write_json(cut, graph)
    prop.write_text(contract(*parameters))
    with log.open("w") as output:
        trial = subprocess.run(
            [
                "yosys",
                "-Q",
                "-T",
                "-p",
                f"read_json {cut}; read_verilog -formal {prop}; prep -top ResetRelease -flatten; "
                "check -assert; opt; sat -prove resetMatches 1 -verify; "
                "sat -prove releaseMatches 1 -verify; "
                f"sat -prove branchCanDiffer 0 -falsify -show branchCanDiffer -dump_json {witness}",
            ],
            stdout=output,
            stderr=subprocess.STDOUT,
            timeout=180,
        )
    text = log.read_text()
    memory_composition.sealed(premise)
    stable = before == hashes(files)
    passed = (
        stable
        and trial.returncode == 0
        and text.count("no model found: SUCCESS!") == 2
        and "model found: FAIL!" in text
        and witness.is_file()
    )
    write_json(
        DIRECTORY / "result.json",
        {
            "passed": passed,
            "inputs_unchanged": stable,
            "inputs": before,
            "artifacts": hashes([p for p in (cut, prop, log, witness) if p.is_file()]),
            "scope": "Any positive number of cold-reset edges from the declared simulation constructor, with quiet NMI. Arbitrary enable, IRQ and response bytes during reset. During release with quiet interrupts and no warm requests, every request and PC agree and all 168 state bits coalesce after three accepted edges. The identical-state disabled-edge theorem permits quiet intervening waits.",
            "limits": "CPU-level reset release. Parent cold-reset pins and initial response values, the first LDA handoff, warm restart and game opcode reachability require separate composition.",
            "universal_game_equivalence": False,
        },
    )
    if not passed:
        raise ValueError("CPU reset release relation incomplete; see " + str(log))
    print("CPU reset release relation: PASS", flush=True)


if __name__ == "__main__":
    qualify()
