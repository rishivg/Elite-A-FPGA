"""Check that extra quiet cold-reset edges preserve all actual CPU state."""

import copy
import json
import shutil
import subprocess
from pathlib import Path

from tools.bbc.proofs import memory_composition
from tools.bbc.proofs.video_cursor import hashes
from tools.validation.common import ROOT, write_json

DIRECTORY = ROOT / "build/bbc/cpu-reset-repetition"


def reset_cut(graph):
    body = copy.deepcopy(graph["modules"]["BbcCpu"])
    clock, reset = body["ports"]["clk"]["bits"], body["ports"]["reset"]["bits"]
    state, following, async_values = [], [], {}
    for name, cell in list(body["cells"].items()):
        if cell["type"] not in ("$dff", "$adff"):
            if any(token in cell["type"].lower() for token in ("ff", "latch", "mem")):
                raise ValueError("Uninventoried CPU storage in reset cut")
            continue
        ports = cell["connections"]
        if ports["CLK"] != clock or int(cell["parameters"]["CLK_POLARITY"], 2) != 1:
            raise ValueError("Unexpected CPU clock")
        if len(ports["Q"]) != len(ports["D"]):
            raise ValueError("Changed CPU register width")
        state.extend(ports["Q"])
        if cell["type"] == "$adff":
            if ports["ARST"] != reset or int(cell["parameters"]["ARST_POLARITY"], 2) != 1:
                raise ValueError("Unexpected CPU asynchronous reset")
            values = list(reversed(cell["parameters"]["ARST_VALUE"]))
            if len(values) != len(ports["Q"]) or set(values) - {"0", "1"}:
                raise ValueError("Undefined asynchronous reset value")
            following.extend(values)
            async_values.update(zip(ports["Q"], values, strict=True))
        else:
            following.extend(ports["D"])
        del body["cells"][name]
    if len(state) != 168 or len(set(state)) != 168:
        raise ValueError("Incomplete reset state inventory")
    initial = {}
    for wire in body["netnames"].values():
        value = wire["attributes"].pop("init", None)
        if value is not None:
            if len(value) != len(wire["bits"]):
                raise ValueError("Changed initializer width")
            for bit, char in zip(wire["bits"], reversed(value), strict=True):
                if bit in state and char in "01":
                    if bit in initial and initial[bit] != int(char):
                        raise ValueError("Conflicting CPU initializers")
                    initial[bit] = int(char)
    constructor = sum(initial.get(bit, 0) << i for i, bit in enumerate(state))
    mask = sum(1 << state.index(bit) for bit in async_values)
    values = sum(int(value) << state.index(bit) for bit, value in async_values.items())
    body["ports"]["state_in"] = {"direction": "input", "bits": state}
    body["ports"]["following"] = {"direction": "output", "bits": following}
    body["attributes"]["top"] = "0"
    return {"modules": {"ResetOnlyTransition": body}}, constructor, mask, values


def qualify():
    parent = ROOT / "build/bbc/cpu-nmi-idle-summary/result.json"
    memory_composition.sealed(parent)
    raw = parent.parent / "positive/registers.json"
    DIRECTORY.mkdir(parents=True, exist_ok=True)
    files = [
        Path(__file__),
        parent,
        raw,
        Path(memory_composition.__file__),
        Path(__file__).parents[1] / "video_cursor.py",
        ROOT / "tools/validation/common.py",
        Path(shutil.which("yosys")),
    ]
    before = hashes(files)
    graph, constructor, mask, values = reset_cut(json.loads(raw.read_text()))
    cut = DIRECTORY / "cut.json"
    write_json(cut, graph)
    prop = DIRECTORY / "contract.v"
    prop.write_text(f"""module ResetRepetition(output equivalent);
(* anyseq *) reg [167:0] state;
(* anyseq *) reg e0,e1,irq0,irq1;
(* anyseq *) reg [7:0] d0,d1;
wire [167:0] normalized=(state&~168'h{mask:042x})|168'h{values:042x};
wire [167:0] constructor=(168'h{constructor:042x}&~168'h{mask:042x})|168'h{values:042x};
wire [167:0] first,second,c0,c1;
ResetOnlyTransition a(.clk(1'b0),.reset(1'b1),.enable(e0),.irq(irq0),.nmi(1'b0),.warm_reset(1'b0),.read_data(d0),.state_in(normalized),.following(first));
ResetOnlyTransition b(.clk(1'b0),.reset(1'b1),.enable(e1),.irq(irq1),.nmi(1'b0),.warm_reset(1'b0),.read_data(d1),.state_in(first),.following(second));
ResetOnlyTransition c(.clk(1'b0),.reset(1'b1),.enable(e0),.irq(irq0),.nmi(1'b0),.warm_reset(1'b0),.read_data(d0),.state_in(constructor),.following(c0));
ResetOnlyTransition d(.clk(1'b0),.reset(1'b1),.enable(e1),.irq(irq1),.nmi(1'b0),.warm_reset(1'b0),.read_data(d1),.state_in(constructor),.following(c1));
assign equivalent=first==second && c0==c1;
endmodule
""")
    graph_path, log = DIRECTORY / "graph.json", DIRECTORY / "proof.log"
    with log.open("w") as output:
        trial = subprocess.run(
            [
                "yosys",
                "-Q",
                "-T",
                "-p",
                f"read_json {cut}; read_verilog -formal -sv {prop}; prep -top ResetRepetition -flatten; check -assert; opt; write_json {graph_path}; sat -prove equivalent 1 -verify -timeout 120",
            ],
            stdout=output,
            stderr=subprocess.STDOUT,
            timeout=180,
        )
    module = json.loads(graph_path.read_text())["modules"]["ResetRepetition"]
    if any(cell["type"] in ("$assume", "$check") for cell in module["cells"].values()):
        raise ValueError("Reset repetition acquired an assumption")
    memory_composition.sealed(parent)
    stable = before == hashes(files)
    passed = stable and trial.returncode == 0 and "SUCCESS!" in log.read_text()
    write_json(
        DIRECTORY / "result.json",
        {
            "passed": passed,
            "inputs_unchanged": stable,
            "inputs": before,
            "artifacts": hashes([cut, prop, graph_path, log]),
            "register_bits": 168,
            "excluded_register_bits": 0,
            "constructor": f"{constructor:042x}",
            "async_mask": f"{mask:042x}",
            "async_values": f"{values:042x}",
            "scope": "All-168-bit actual CPU reset idempotence with NMI quiet: one reset edge followed by another yields identical complete state for arbitrary preceding legal state, enables, IRQ and read bytes. Asynchronous state is normalized to its real reset value before sampling. First reset from the declared zero-simulation constructor with explicit HDL initializers is independent of enable, IRQ and data. This permits any positive number of held reset edges and arbitrary enables before release.",
            "limits": "Reset repetition only. Does not cover warm requests, nonquiet NMI, parent reset timing or universal whole-game equivalence.",
            "universal_game_equivalence": False,
        },
    )
    print("Complete CPU reset repetition: " + ("PASS" if passed else "FAIL"), flush=True)
    if not passed:
        raise ValueError("Reset repetition incomplete")


if __name__ == "__main__":
    qualify()
