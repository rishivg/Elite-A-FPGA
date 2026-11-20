"""Derive accepted-clock stream equivalence from complete CPU idle preservation."""

import argparse
import json
import subprocess
from pathlib import Path

from tools.bbc import build, cpu_instruction_proof
from tools.validation.common import ROOT, digest, write_json


def sealed(path):
    """Check formal receipts without imposing a finite clock-corpus schema."""
    report = json.loads(path.read_text())
    if report.get("passed") is not True or report.get("inputs_unchanged") is not True:
        raise ValueError("Incomplete formal premise")
    for group in ("inputs", "artifacts"):
        files = report.get(group, {})
        if not files or files != build.hashes([ROOT / name for name in files]):
            raise ValueError("Changed formal premise: " + group)
    version = subprocess.check_output(["yosys", "-V"], text=True).strip()
    if report.get("yosys", report.get("tools", {}).get("yosys")) != version:
        raise ValueError("Formal premise uses another Yosys version")
    return report


def qualify(stream="cpu-memory-induction-proof"):
    stream_path = build.BBC / stream / "result.json"
    idle_path = build.BBC / "cpu-idle-proof/result.json"
    enabled = sealed(stream_path)
    idle = sealed(idle_path)
    if (
        enabled.get("instruction_count") != "unbounded"
        or enabled.get("enable_period") != 1
        or enabled.get("universal_game_equivalence") is not False
        or idle.get("register_bits") != 168
        or idle.get("excluded_register_bits") != 0
    ):
        raise ValueError(
            "Missing unbounded enabled semantics or complete physical-state idle proof"
        )
    # Both premises must describe precisely the same current CPU, including its embedded ALU.
    for source in ("rtl/bbc/BbcCpu.v", "build/bbc/ArletCpuEnabled.v"):
        if enabled["inputs"][source] != idle["inputs"][source] or idle["inputs"][source] != digest(
            ROOT / source
        ):
            raise ValueError("Enabled and idle premises use different CPU sources")
    graph_path = build.BBC / "cpu-idle-proof/registers.json"
    module = json.loads(graph_path.read_text())["modules"]["BbcCpu"]
    clock = module["ports"]["clk"]["bits"]
    consumers = []
    state_bits = []
    for name, cell in module["cells"].items():
        if cell["type"] in {"$dff", "$adff"}:
            state_bits.extend(cell["connections"]["Q"])
        elif any(kind in cell["type"].lower() for kind in ("ff", "latch", "mem")):
            raise ValueError("Uninventoried CPU state")
        for port, bits in cell["connections"].items():
            if cell["port_directions"][port] == "input" and clock[0] in bits:
                if (
                    cell["type"] not in {"$dff", "$adff"}
                    or port != "CLK"
                    or int(cell["parameters"]["CLK_POLARITY"], 2) != 1
                ):
                    raise ValueError("CPU clock has a combinational or negative-edge consumer")
                consumers.append(name)
    if (
        len(state_bits) != 168
        or len(set(state_bits)) != 168
        or not consumers
        or any(clock[0] in port["bits"] for name, port in module["ports"].items() if name != "clk")
    ):
        raise ValueError("Incomplete complete-state/clock binding")
    inputs = [
        Path(__file__),
        stream_path,
        idle_path,
        graph_path,
        Path(cpu_instruction_proof.__file__),
    ]
    before = build.hashes(inputs)
    sealed(stream_path)
    sealed(idle_path)
    report = {
        "passed": before == build.hashes(inputs),
        "inputs": before,
        "inputs_unchanged": before == build.hashes(inputs),
        "premises": {
            "enabled": str(stream_path.relative_to(ROOT)),
            "idle": str(idle_path.relative_to(ROOT)),
        },
        "register_bits": 168,
        "excluded_register_bits": 0,
        "opcodes": enabled["opcodes"],
        "instruction_count": "unbounded",
        "enable_schedule": "Arbitrary inserted disabled edges, including periods 16/48 and arbitrarily long waits",
        "derivation": [
            "After the ordinary reset/prefix, NMI and its directly clocked previous sample remain zero; reset/warm-reset/IRQ stay inactive.",
            "The exact D/Q proof preserves all 168 physical state bits on every disabled edge for arbitrary read_data. The actual clock has only positive-edge sequential consumers.",
            "Erase disabled edges. Each remaining edge applies the same deterministic current-CPU transition to the same state and accepted read response as the period-one stream contract.",
            "Induction on accepted edges therefore gives the same state and accepted bus request/write value. Physical timestamps are stretched by the chosen schedule, without executing fewer DUT edges in gameplay tests.",
        ],
        "read_response_requirement": "At each enabled edge, read_data is the response to the preceding accepted request, exactly as in the stream prefix. It may change during disabled edges, which preserve all state. Response completion before acceptance is a separate parent-memory/device obligation.",
        "scope": "A composition theorem over accepted CPU edges. Its premises are actual unbounded instruction induction and an exhaustive complete-state D/Q idle proof. It introduces no state/input assumptions into either SAT proof and performs no internal state injection.",
        "limits": "Same initialization and opcode class as the enabled premise. Inactive IRQ/NMI and warm reset. No arbitrary interrupt/reset histories, comparison of idle bus wires to Original, parent-clock/memory/device composition, physical timing proof, or universal whole-game equivalence. Gameplay captures continue to execute every master edge.",
        "universal_cpu_equivalence": False,
        "universal_game_equivalence": False,
    }
    output = (
        build.BBC / (stream.replace("-induction-proof", "") + "-enable-composition") / "result.json"
    )
    write_json(output, report)
    if not report["passed"]:
        raise ValueError("Enable composition inputs changed")
    print(
        f"Derived arbitrary enable spacing for {len(report['opcodes'])} documented opcodes",
        flush=True,
    )
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stream", default="cpu-memory-induction-proof")
    qualify(parser.parse_args().stream)
