"""Compose unbounded instruction semantics with exact physical NMI wait transitions."""

import argparse
import json
from pathlib import Path

from tools.bbc import build
from tools.bbc.proofs import enable_composition
from tools.validation.common import ROOT, digest, write_json


def qualify(cold=False):
    stream_directory = (
        "cpu-cold-nmi-macro-stream-investigation" if cold else "cpu-nmi-macro-stream-investigation"
    )
    paths = {
        "stream": build.BBC / stream_directory / "result.json",
        "wait": build.BBC / "cpu-nmi-idle-summary/result.json",
        "accepted": build.BBC / "cpu-nmi-macro-transition-proof/result.json",
    }
    premises = {name: enable_composition.sealed(path) for name, path in paths.items()}
    stream, wait, accepted = (premises[name] for name in ("stream", "wait", "accepted"))
    if (
        stream.get("instruction_count") != "unbounded"
        or len(stream.get("opcodes", [])) != 151
        or stream.get("admitted_opcode_domains") != 22801
        or stream.get("excluded_opcode_positions") != 210
        or stream.get("proof_graph_rebuilt_from_current_sources_identically") is not True
        or accepted.get("inactive_nmi_initialization_all_dummy_phases") is not True
        or any(
            p.get("register_bits") != 168 or p.get("excluded_register_bits") != 0
            for p in (wait, accepted)
        )
    ):
        raise ValueError("Incomplete interrupt stream or physical transition premise")
    for source in ("rtl/bbc/BbcCpu.v", "build/bbc/ArletCpuEnabled.v"):
        current = digest(ROOT / source)
        if any(p["inputs"].get(source) != current for p in premises.values()):
            raise ValueError("Composition premises refer to different actual CPUs")
    for name in ("macro-core.v", "macro-wrapper.v"):
        streamed = build.BBC / stream_directory / name
        local = build.BBC / "cpu-nmi-macro-transition-proof" / name
        if digest(streamed) != digest(local):
            raise ValueError("Stream and physical transition use different virtual CPUs")
    graph = build.BBC / "cpu-nmi-idle-summary/positive/registers.json"
    module = json.loads(graph.read_text())["modules"]["BbcCpu"]
    clock = module["ports"]["clk"]["bits"]
    bits, consumers = [], []
    for name, cell in module["cells"].items():
        if cell["type"] in {"$dff", "$adff"}:
            bits.extend(cell["connections"]["Q"])
        elif any(kind in cell["type"].lower() for kind in ("ff", "latch", "mem")):
            raise ValueError("Uninventoried physical CPU state")
        for port, connection in cell["connections"].items():
            if cell["port_directions"][port] == "input" and clock[0] in connection:
                if (
                    cell["type"] not in {"$dff", "$adff"}
                    or port != "CLK"
                    or int(cell["parameters"]["CLK_POLARITY"], 2) != 1
                ):
                    raise ValueError("Combinational or falling-edge CPU clock consumer")
                consumers.append(name)
    if len(bits) != 168 or len(set(bits)) != 168 or not consumers:
        raise ValueError("Incomplete physical CPU inventory")
    inputs = [Path(__file__), Path(enable_composition.__file__), *paths.values(), graph]
    before = build.hashes(inputs)
    # Recheck every nested source and proof artifact after assembling the theorem.
    for path in paths.values():
        enable_composition.sealed(path)
    stable = before == build.hashes(inputs)
    report = {
        "passed": stable,
        "inputs": before,
        "inputs_unchanged": stable,
        "artifacts": build.hashes([graph, build.BBC / stream_directory / "graph.json"]),
        "tools": accepted["tools"],
        "premises": {name: str(path.relative_to(ROOT)) for name, path in paths.items()},
        "register_bits": 168,
        "excluded_register_bits": 0,
        "opcodes": stream["opcodes"],
        "instruction_count": "unbounded",
        "enable_schedule": "Arbitrary inserted disabled master edges",
        "initialization": "Direct cold reset" if cold else "Ordinary initialization prefix",
        "arbitrary_irq_after_prefix": True,
        "arbitrary_nmi_on_every_master_edge_after_prefix": True,
        "derivation": [
            "The virtual and actual CPUs execute the same inactive-NMI initialization: the exact D/Q initialization lemma includes every cold dummy phase, and their reset drivers and values are unchanged by the event transformation.",
            "Partition the physical history into blocks of disabled edges followed by accepted edges. Every wait block preserves 165 bits, accumulates any sampled NMI rise into pending/unaged state, and retains the final pin sample; the complete-state wait theorem covers blocks of any length.",
            "At acceptance, the exact all-168-bit D/Q and functional-output theorem replaces that block with its event and the fresh pin rise. IRQ and accepted read data remain unrestricted.",
            "The unbounded 151-opcode virtual stream proof admits every opcode pair and free wait/fresh-rise events. Physical event combinations are a subset of that unrestricted domain.",
            "Induction on accepted edges therefore yields the same accepted addresses, write direction/bytes, instruction retirement PC and architectural registers/flags for every such physical wait/interrupt history.",
        ],
        "read_response_requirement": "At each accepted edge, read_data responds to the preceding accepted request. It may change during waits. Parent response completion and physical timestamps remain separate obligations.",
        "scope": "Composition of source-identical unbounded instruction induction, all-state disabled-clock equations and exact accepted-edge transition equivalence. No DUT state injection, extra SAT assumptions or bounded wait limit.",
        "limits": (
            "Direct cold reset; IRQ/NMI inactive through its reset-vector entry. "
            if cold
            else "Ordinary initialization prefix. "
        )
        + "Documented opcodes and zero simulation power-on profile. Reset/warm reset inactive afterward. Accepted bus observations only; no Original idle-wire comparison, parent/device/video timing or universal whole-game equivalence claim.",
        "universal_cpu_equivalence": False,
        "universal_game_equivalence": False,
    }
    if cold:
        report["derivation"][0] = (
            "The virtual CPU executes eight real reset edges and all seven NMOS reset bus cycles, without a register/stack/PLP initialization prefix. IRQ/NMI are inactive until vector entry. The complete D/Q initialization lemma covers every dummy phase; inactive-NMI disabled edges preserve all state. Removing or inserting such initial waits gives the same cold sequence and accepted read responses. Reset drivers/values are unchanged by the event transformation."
        )
    output = "cpu-cold-nmi-enable-composition" if cold else "cpu-nmi-enable-composition"
    write_json(build.BBC / output / "result.json", report)
    if not stable:
        raise ValueError("NMI enable composition inputs changed")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cold", action="store_true", help="Use the direct cold stream premise")
    qualify(cold=parser.parse_args().cold)
