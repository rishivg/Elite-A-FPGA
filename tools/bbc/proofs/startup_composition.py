"""Compose current startup flag, interrupt, ROM and scheduling premises."""

import json
from pathlib import Path

from tools.bbc import build, bus_binding_proof
from tools.bbc.proofs import enable_composition, memory_composition
from tools.validation.common import ROOT, digest, write_json


def qualify():
    names = (
        "os-startup-zero-flag-bridge",
        "os-startup-zero-flag-waits",
        "os-startup-zero-flag-fault",
        "os-startup-zero-flag-waits-fault",
        "startup-interrupt-quiet-proof",
    )
    paths = [build.BBC / name / "result.json" for name in names]
    reports = {name: enable_composition.sealed(path) for name, path in zip(names, paths)}
    memory_path = build.BBC / "memory-response-composition/result.json"
    memory = json.loads(memory_path.read_text())
    if memory.get("passed") is not True or memory.get("inputs_unchanged") is not True:
        raise ValueError("Incomplete memory composition")
    if memory["inputs"] != build.hashes([ROOT / name for name in memory["inputs"]]):
        raise ValueError("Changed memory composition")
    expected_memory = {
        "ram-proof",
        "rom-proof",
        "memory-binding-proof",
        "cpu-request-deadline-proof",
        "memory-response-deadline-proof",
    }
    if set(memory["premises"]) != expected_memory:
        raise ValueError("Changed memory composition domain")
    for path in memory["premises"].values():
        memory_composition.sealed(ROOT / path)
    bridge = reports["os-startup-zero-flag-bridge"]
    waits = reports["os-startup-zero-flag-waits"]
    devices = reports["startup-interrupt-quiet-proof"]
    if not (
        bridge["register_bits"] == waits["register_bits"] == 168
        and bridge["excluded_register_bits"] == waits["excluded_register_bits"] == 0
        and bridge["only_reference_difference"] == ["core.Z"]
        and {p["clock_multiplier"] for p in devices["clocks"]} == {1, 3}
    ):
        raise ValueError("Incomplete startup state or clock domain")
    parents = [build.BBC / "BbcMachine.v", build.BBC / "full-video/BbcMachine96.v"]
    for parent, top in zip(parents, ("BbcMachine", "BbcMachine96")):
        name = str(parent.relative_to(ROOT))
        if devices["inputs"].get(name) != digest(parent) or memory["inputs"].get(name) != digest(
            parent
        ):
            raise ValueError("Startup premises bind different actual parents")
        cpu = bus_binding_proof.ports(
            bus_binding_proof.module(parent.read_text(), top), "BbcCpu", "cpu"
        )
        for port, required in (("clk", "clk"), ("reset", "reset"), ("enable", "cpuEnable")):
            if cpu[port].replace(" ", "") != required:
                raise ValueError("Changed actual CPU clock/reset/acceptance binding")
    files = [
        Path(__file__),
        Path(enable_composition.__file__),
        Path(memory_composition.__file__),
        Path(bus_binding_proof.__file__),
        memory_path,
        *paths,
        *parents,
    ]
    before = build.hashes(files)
    for path in paths:
        enable_composition.sealed(path)
    stable = before == build.hashes(files)
    directory = build.BBC / "os-startup-profile-composition"
    directory.mkdir(exist_ok=True)
    report = {
        "passed": stable,
        "inputs": before,
        "inputs_unchanged": stable,
        "premises": [str(p.relative_to(ROOT)) for p in (*paths, memory_path)],
        "derivation": [
            "Before the first accepted CPU write, each actual parent is in the read-only prefix domain. The actual VIA, serial and disc theorem supplies inactive IRQ/NMI independently of CPU semantics, including arbitrary warm requests and abstracted pin histories.",
            "The actual memory and routing premises supply accepted ROM responses from the verified original image. They discharge the startup bridge's four fixed reset-vector/opcode/operand bytes; other responses remain unrestricted.",
            "Both actual parent CPU ports have the same reset/clock bindings and use the proved scheduler enable. Its physical history is admitted by the startup bridge's arbitrary enabled/disabled edges.",
            "The paired physical CPU theorem therefore guarantees the same startup bus observations and convergence of all 168 state bits after the first LDA retirement, including warm requests, and before any write. A warm request may postpone that retirement indefinitely; convergence is conditional on retirement, without a liveness assertion.",
            "After convergence, identical physical CPU next-state functions preserve equality under shared future inputs. The separate unbounded instruction and parent/device contracts govern their subsequent meaning; no game-level conclusion is inferred here.",
        ],
        "scope": "Discharge the inactive-interrupt, verified-ROM and actual-clock premises of the initial-zero-flag normalization bridge at both actual parent rates. Source/artifact seals and exact parent/CPU port bindings are checked. No DUT state injection or finite capture inference.",
        "limits": "Specified simulation power-on profiles differing only in CPU Z, initial cold reset, no subsequent cold reset and shared future inputs. This is an initial-profile composition, not a proof of independent reference CPU/device algorithms or universal whole-game equivalence.",
        "universal_game_equivalence": False,
    }
    write_json(directory / "result.json", report)
    if not stable:
        raise ValueError("Startup composition inputs changed")
    return report


if __name__ == "__main__":
    qualify()
