"""Check every pair of documented immediate instructions without state injection."""

import json
import subprocess

from tools.bbc import build
from tools.validation.common import ROOT, digest, write_json

OPCODES = (0x09, 0x29, 0x49, 0x69, 0xE9, 0xC9, 0xE0, 0xC0, 0xA9, 0xA2, 0xA0)
DIRECTORY = build.BBC / "cpu-immediate-pair-proof"
TOP = "BbcCpuImmediatePairContract"


def guard_domains(graph):
    """Ensure the sole assumption restricts only the two symbolic opcodes."""
    module = graph["modules"][TOP]
    domain = set(module["netnames"]["opcode"]["bits"] + module["netnames"]["second"]["bits"])
    assumptions = [c for c in module["cells"].values() if c["type"] == "$assume"]
    if len(domain) != 16 or len(assumptions) != 1 or assumptions[0]["connections"]["EN"] != ["1"]:
        raise ValueError("Expected one unconditional two-opcode assumption")
    drivers = {}
    for cell in module["cells"].values():
        for port, direction in cell["port_directions"].items():
            if direction == "output":
                for bit in cell["connections"][port]:
                    if isinstance(bit, int):
                        if bit in drivers:
                            raise ValueError("Multiple formal signal drivers")
                        drivers[bit] = cell
    pending, seen, leaves = list(assumptions[0]["connections"]["A"]), set(), set()
    while pending:
        bit = pending.pop()
        if bit in {"0", "1"} or bit in seen:
            continue
        seen.add(bit)
        if bit in domain:
            if drivers[bit]["type"] != "$anyconst":
                raise ValueError("Opcode is not a free symbolic constant")
            leaves.add(bit)
            continue
        cell = drivers.get(bit)
        if cell is None or any(t in cell["type"].lower() for t in ("ff", "mem", "any", "latch")):
            raise ValueError("Domain assumption restricts state or another input")
        for port, direction in cell["port_directions"].items():
            if direction == "input":
                pending.extend(cell["connections"][port])
    if leaves != domain:
        raise ValueError("Incomplete symbolic opcode domains")


def qualify():
    if not build.generation_current():
        raise ValueError("Run make bbc-gen before proving current CPU RTL")
    DIRECTORY.mkdir(parents=True, exist_ok=True)
    core = build.BBC / "ArletCpuEnabled.v"
    formal = ROOT / "sim/formal"
    common = [
        ROOT / "rtl/bbc/BbcCpu.v",
        ROOT / "third_party/6502/ALU.v",
        formal / "BbcCpuContractPrefix.v",
        formal / "BbcCpuArchitectureContract.v",
        formal / "BbcCpuImmediatePairContract.v",
    ]
    inputs = [ROOT / "tools/bbc/proofs/immediate_pairs.py", core, *common]
    before = build.hashes(inputs)
    actual = core.read_text()
    target = "adc_bcd <= (plp ? ADD[3] : cld ? 1'b0 : sed ? 1'b1 : D) && state != BRK0;"
    if actual.count(target) != 1:
        raise ValueError("Missing decimal forwarding negative-control target")
    fault = DIRECTORY / "old-decimal.v"
    fault.write_text(actual.replace(target, "adc_bcd <= D;"))
    results, artifacts = [], [fault]
    graph = DIRECTORY / "positive.json"
    for name, model in (("positive", core), ("old-decimal", fault)):
        log = DIRECTORY / (name + ".log")
        command = (
            "read_verilog -formal "
            + " ".join(map(str, [model, *common]))
            + f"; prep -top {TOP} -flatten; memory_map; opt; async2sync; opt_clean; "
            + (f"write_json {graph}; " if name == "positive" else "")
            + "sat -set-assumes -set-init-zero -seq 42 -prove equivalent 1 -verify -timeout 180"
        )
        with log.open("w") as output:
            run = subprocess.run(
                ["yosys", "-Q", "-T", "-p", command],
                cwd=ROOT,
                stdout=output,
                stderr=subprocess.STDOUT,
                timeout=240,
            )
        text = log.read_text()
        accepted = (
            run.returncode == 0 and "SAT proof finished - no model found: SUCCESS!" in text
            if name == "positive"
            else run.returncode != 0 and "SAT proof finished - model found: FAIL!" in text
        )
        results.append({"name": name, "accepted": accepted, "returncode": run.returncode})
        artifacts.append(log)
        print(results[-1], flush=True)
    guard_domains(json.loads(graph.read_text()))
    commands = "; ".join(
        f"sat -set-assumes -set-init-zero -seq 1 -set opcode {a} -set second {b} -show opcode -show second"
        for a in OPCODES
        for b in OPCODES
    )
    domains = DIRECTORY / "domains.log"
    with domains.open("w") as output:
        run = subprocess.run(
            ["yosys", "-Q", "-T", "-p", f"read_json {graph}; hierarchy -top {TOP}; " + commands],
            cwd=ROOT,
            stdout=output,
            stderr=subprocess.STDOUT,
            timeout=240,
        )
    admitted = (
        run.returncode == 0
        and domains.read_text().count("SAT solving finished - model found:") == 121
    )
    stable = before == build.hashes(inputs) and build.generation_current()
    report = {
        "passed": stable and admitted and all(r["accepted"] for r in results),
        "inputs": before,
        "inputs_unchanged": stable,
        "proofs": results,
        "nonempty_domains": admitted,
        "opcode_pairs": 121,
        "physical_edges": 42,
        "enable_period": 1,
        "tools": {"yosys": subprocess.check_output(["yosys", "-V"], text=True).strip()},
        "artifacts": build.hashes([*artifacts, graph, domains]),
        "contract_sha256": digest(common[-1]),
        "scope": "Bounded exhaustive two-instruction composition after actual reset and normal register/stack-loading prefix. Every documented immediate opcode pair, symbolic A/X/Y/S/P, reset PC and both operands. Exact bus requests, write direction, retirement PCs and architectural state after each instruction. Independent documented NMOS byte/nibble semantics. Sole assumption restricts only symbolic opcodes; every pair has a satisfiable initialized domain. Actual stale-decimal-forwarding mutation must fail. No internal CPU or Original state is injected.",
        "limits": "Fixed ordinary initialization prefix, inactive IRQ/NMI, enable period 1, zero simulation power-on profile. Shared synthetic synchronous bus responses. No arbitrary prior hidden-state induction, undocumented opcode proof or universal whole-game equivalence.",
        "universal_cpu_equivalence": False,
        "universal_game_equivalence": False,
    }
    write_json(DIRECTORY / "result.json", report)
    if not report["passed"]:
        raise ValueError("Immediate-pair proof failed; all RTL and logs retained")
    return report


if __name__ == "__main__":
    qualify()
