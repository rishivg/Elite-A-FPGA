"""Check that a quiet falling clock edge cannot affect actual machine RTL."""

import json
import re
import subprocess
from pathlib import Path

from tools.validation.common import ROOT, write_json

from . import build, video96

FLOPS = {"$dff", "$dffe", "$adff", "$adffe", "$sdff", "$sdffe", "$sdffce"}


def clock_property(graph, top):
    """Inspect every root-clock use after flattening; reject unsupported uses."""
    module = graph["modules"][top]
    clocks = module["ports"]["clk"]["bits"]
    if len(clocks) != 1 or not isinstance(clocks[0], int):
        raise ValueError("Expected one actual root clock bit")
    clock = clocks[0]
    for name, port in module["ports"].items():
        if port["direction"] == "output" and clock in port["bits"]:
            raise ValueError("Root clock directly affects output: " + name)
    uses = []
    for name, cell in module["cells"].items():
        kind = cell["type"]
        if not kind.startswith("$"):
            raise ValueError("Unflattened or blackboxed machine cell: " + name)
        for port, bits in cell["connections"].items():
            indices = [index for index, bit in enumerate(bits) if bit == clock]
            if not indices:
                continue
            parameters = cell["parameters"]
            if kind in FLOPS and port == "CLK":
                if bits != [clock] or int(parameters["CLK_POLARITY"], 2) != 1:
                    raise ValueError("Nonpositive root-clock flip-flop: " + name)
            elif kind == "$mem_v2" and port in {"RD_CLK", "WR_CLK"}:
                prefix = port.removesuffix("_CLK")
                enabled = int(parameters[prefix + "_CLK_ENABLE"], 2)
                positive = int(parameters[prefix + "_CLK_POLARITY"], 2)
                if any(not ((enabled >> index) & (positive >> index) & 1) for index in indices):
                    raise ValueError("Unsupported root-clock memory port: " + name)
            else:
                raise ValueError("Combinational, latch or unsupported root-clock use: " + name)
            uses.append({"cell": name, "type": kind, "port": port, "indices": indices})
    if not uses:
        raise ValueError("No actual machine state clocks found")
    return uses


def qualify(multiplier=1):
    if multiplier not in {1, 3} or not build.generation_current():
        raise ValueError("Generate the current actual machine before checking clock uses")
    if multiplier == 3:
        video96.generate()
    top = "BbcMachine" if multiplier == 1 else "BbcMachine96"
    parent = build.BBC if multiplier == 1 else video96.DIRECTORY
    source = parent / (top + ".v")
    directory = build.BBC / f"negative-edge-proof{multiplier}"
    directory.mkdir(exist_ok=True)
    dependencies = [
        source,
        ROOT / "rtl/bbc/BbcCpu.v",
        build.BBC / "ArletCpuEnabled.v",
        ROOT / "third_party/6502/ALU.v",
        Path(__file__),
        *parent.glob(top + ".v_*.bin"),
    ]
    before = build.hashes(dependencies)

    def flatten(rtl, label):
        graph = directory / (label + ".json")
        command = (
            "read_verilog "
            + " ".join(str(path) for path in [rtl, *dependencies[1:4]])
            + f"; hierarchy -check -top {top}; proc; flatten; memory_collect; "
            + f"opt_clean; check -assert; write_json {graph}"
        )
        with (directory / (label + ".log")).open("w") as log:
            subprocess.run(
                ["yosys", "-Q", "-T", "-p", command],
                cwd=parent,
                stdout=log,
                stderr=subprocess.STDOUT,
                check=True,
                timeout=120,
            )
        return json.loads(graph.read_text())

    uses = clock_property(flatten(source, "actual"), top)
    text = source.read_text()
    match = re.search(r"^module\s+" + top + r"\b[\s\S]*?^endmodule\b", text, re.M)
    if match is None:
        raise ValueError("Missing actual parent for negative control")
    parent_text = match[0]
    header_end = parent_text.index(");")
    wrong = (
        parent_text[:header_end]
        + ",\n output reg proofNegative\n"
        + parent_text[header_end:].removesuffix("endmodule")
        + "always @(negedge clk or posedge reset)\n"
        + " if (reset) proofNegative <= 0; else proofNegative <= ~proofNegative;\nendmodule"
    )
    negative = directory / "wrong-negative-edge.v"
    negative.write_text(text[: match.start()] + wrong + text[match.end() :])
    detected = False
    try:
        clock_property(flatten(negative, "negative"), top)
    except ValueError as error:
        detected = "Nonpositive root-clock flip-flop" in str(error)
    unchanged = before == build.hashes(dependencies) and build.generation_current()
    report = {
        "passed": unchanged and detected,
        "inputs": before,
        "clock_multiplier": multiplier,
        "clock_uses": uses,
        "negative_control_detected": detected,
        "inputs_unchanged": unchanged,
        "artifacts": build.hashes(
            [
                directory / (name + suffix)
                for name in ["actual", "negative"]
                for suffix in [".json", ".log"]
            ]
            + [negative]
        ),
        "scope": "Exact flattened actual machine: every use of the root clock is a positive-edge state/memory clock. It drives no combinational input, latch or output. With other inputs stable and reset inactive, a falling edge has no RTL state or output effect. No functional child blackboxes.",
        "limits": "A structural RTL property, not a proof of a C++ simulator scheduler shortcut or full game equivalence. Input changes and every positive edge still require normal evaluation.",
    }
    write_json(directory / "result.json", report)
    print(
        f"Quiet negative-edge RTL property {multiplier}: {report['passed']}; {len(uses)} clock uses",
        flush=True,
    )
    if not report["passed"]:
        raise ValueError("Actual negative-edge property or its negative control failed")
    return report


if __name__ == "__main__":
    qualify(1)
    qualify(3)
