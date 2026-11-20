"""Prove arbitrary-length documented immediate streams after an ordinary reset."""

import json
import subprocess
from pathlib import Path

from tools.bbc import build, cpu_instruction_proof
from tools.validation.common import ROOT, digest, write_json

OPCODES = (0x09, 0x29, 0x49, 0x69, 0xE9, 0xC9, 0xE0, 0xC0, 0xA9, 0xA2, 0xA0)
DIRECTORY = build.BBC / "cpu-immediate-induction-proof"
TOP = "BbcCpuImmediateInduction"


def guard_domains(graph):
    """Ensure the sole assumption restricts only the first and subsequent symbolic opcodes."""
    module = graph["modules"][TOP]
    domain = set(
        module["netnames"]["firstOpcode"]["bits"] + module["netnames"]["nextOpcode"]["bits"]
    )
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
            if drivers[bit]["type"] != (
                "$anyconst" if bit in module["netnames"]["firstOpcode"]["bits"] else "$anyseq"
            ):
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


def qualify(two_cycle=False):
    if not build.generation_current():
        raise ValueError("Run make bbc-gen before proving current CPU RTL")
    directory = build.BBC / "cpu-two-cycle-induction-proof" if two_cycle else DIRECTORY
    opcodes = (
        tuple(
            op
            for op in cpu_instruction_proof.SCALAR_OPCODES
            if op not in (0, 0x10, 0x30, 0x50, 0x70, 0x90, 0xB0, 0xD0, 0xF0)
        )
        if two_cycle
        else OPCODES
    )
    directory.mkdir(parents=True, exist_ok=True)
    core = build.BBC / "ArletCpuEnabled.v"
    formal = ROOT / "sim/formal"
    common = [
        ROOT / "rtl/bbc/BbcCpu.v",
        ROOT / "third_party/6502/ALU.v",
        formal / "BbcCpuStreamPrefix.v",
        formal / "BbcCpuArchitectureContract.v",
        formal
        / ("BbcCpuTwoCycleContract.v" if two_cycle else "BbcCpuImmediateInductiveContract.v"),
    ]
    inputs = [ROOT / "tools/bbc/proofs/immediate_induction.py", core, *common]
    normal = formal / "BbcCpuContractPrefix.v"
    expected = (
        normal.read_text()
        .replace("module BbcCpuContractPrefix(", "module BbcCpuStreamPrefix(")
        .replace("if (age < 40) age <= age + 1;", "if (age < 30) age <= age + 1;")
    )
    if common[2].read_text() != expected:
        raise ValueError("Stream prefix changed beyond its name and final age hold")
    support = [build.BBC / name / "result.json" for name in ("cpu-arithmetic", "cpu-values")]
    for path in support:
        report = cpu_instruction_proof.supporting_evidence(path)
        for category in ("inputs", "artifacts"):
            files = report[category]
            files = files.get("sources", files)
            inputs.extend(ROOT / name for name in files)
    inputs.extend([normal, *support, Path(cpu_instruction_proof.__file__)])
    before = build.hashes(inputs)
    actual = core.read_text()
    target = "adc_bcd <= (plp ? ADD[3] : cld ? 1'b0 : sed ? 1'b1 : D) && state != BRK0;"
    if actual.count(target) != 1:
        raise ValueError("Missing decimal forwarding negative-control target")
    fault = directory / "old-decimal.v"
    fault.write_text(actual.replace(target, "adc_bcd <= D;"))
    results, artifacts = [], [fault]
    graph = directory / "positive.json"
    for name, model in (("positive", core), ("old-decimal", fault)):
        log = directory / (name + ".log")
        command = (
            "read_verilog -formal "
            + " ".join(map(str, [model, *common]))
            + f"; prep -top {TOP} -flatten; memory_map; opt; async2sync; opt_clean; "
            + (f"write_json {graph}; " if name == "positive" else "")
            + "sat -set-assumes -set-init-zero -tempinduct -initsteps 40 -maxsteps 48 -stepsize 4 -prove equivalent 1 -verify -timeout 300"
        )
        with log.open("w") as output:
            run = subprocess.run(
                ["yosys", "-Q", "-T", "-p", command],
                cwd=ROOT,
                stdout=output,
                stderr=subprocess.STDOUT,
                timeout=900,
            )
        text = log.read_text()
        accepted = (
            run.returncode == 0 and "Induction step proven: SUCCESS!" in text
            if name == "positive"
            else run.returncode != 0
            and "SAT temporal induction proof finished - model found for base case: FAIL!" in text
        )
        results.append({"name": name, "accepted": accepted, "returncode": run.returncode})
        artifacts.append(log)
        print(results[-1], flush=True)
    guard_domains(json.loads(graph.read_text()))
    commands = "; ".join(
        f"sat -set-assumes -set-init-zero -seq 1 -set firstOpcode {a} -set nextOpcode {b} -show firstOpcode -show nextOpcode"
        for a in opcodes
        for b in opcodes
    )
    domains = directory / "domains.log"
    with domains.open("w") as output:
        run = subprocess.run(
            ["yosys", "-Q", "-T", "-p", f"read_json {graph}; hierarchy -top {TOP}; " + commands],
            cwd=ROOT,
            stdout=output,
            stderr=subprocess.STDOUT,
            timeout=900,
        )
    admitted = (
        run.returncode == 0
        and domains.read_text().count("SAT solving finished - model found:") == len(opcodes) ** 2
    )
    for path in support:
        cpu_instruction_proof.supporting_evidence(path)
    stable = before == build.hashes(inputs) and build.generation_current()
    report = {
        "passed": stable and admitted and all(r["accepted"] for r in results),
        "inputs": before,
        "inputs_unchanged": stable,
        "proofs": results,
        "nonempty_domains": admitted,
        "admitted_opcode_domains": len(opcodes) ** 2,
        "opcodes": [f"{op:02x}" for op in opcodes],
        "instruction_count": "unbounded",
        "enable_period": 1,
        "induction": "Initialized base cases plus temporal induction, search depths 40/44/48",
        "independent_support": [str(p.relative_to(ROOT)) for p in support],
        "tools": {"yosys": subprocess.check_output(["yosys", "-V"], text=True).strip()},
        "artifacts": build.hashes([*artifacts, graph, domains]),
        "contract_sha256": digest(common[-1]),
        "scope": "Arbitrary-length streams of the eleven documented immediate opcodes after eight actual reset edges and the maintained ordinary register/stack/PLP prefix. Symbolic A/X/Y/S/P and reset PC, freely changing subsequent opcode/operand responses. Exact bus requests and write direction, retirement PCs with sixteen-bit wrap, and A/X/Y/S/flags after every instruction. Independent documented NMOS semantics; initialized base plus temporal induction over the actual CPU and shadow-model state. One unconditional opcode-only assumption; both symbolic opcode sources stay free and all 121 initial/subsequent opcode domains are admitted. Actual stale decimal-forwarding mutation must fail a base case. Current independent arithmetic and all-byte documented-opcode comparisons at enable periods 1/16/48 are sealed separately. No internal CPU state injection.",
        "limits": "Inactive IRQ/NMI, enable period 1, zero simulation power-on profile. Synthetic synchronous read responses need not describe one static memory image. Only immediate opcodes 09/29/49/69/E9/C9/E0/C0/A9/A2/A0; the normal initialization prefix is fixed. No arbitrary documented/undocumented instruction or interrupt-history equivalence, parent-machine composition, or universal Original whole-game proof.",
        "universal_cpu_equivalence": False,
        "universal_game_equivalence": False,
    }
    if two_cycle:
        report["scope"] = (
            report["scope"]
            .replace(
                "eleven documented immediate opcodes",
                "33 documented two-cycle immediate/implied opcodes",
            )
            .replace(
                "all 121 initial/subsequent opcode domains",
                "all 1,089 initial/subsequent opcode domains",
            )
        )
        report["scope"] += (
            " One- and two-byte instruction lengths are distinguished, including repeated dummy-read addresses. CLI/SEI's early I update is compared through an explicit prefetch/decode flag relation; every diagnostic flag bit remains checked."
        )
        report["limits"] = report["limits"].replace(
            "Only immediate opcodes 09/29/49/69/E9/C9/E0/C0/A9/A2/A0",
            "Only the 33 declared documented two-cycle opcodes; CLI/SEI's I result uses the stated diagnostic pipeline relation",
        )
    write_json(directory / "result.json", report)
    if not report["passed"]:
        raise ValueError("Immediate-stream induction failed; all RTL and logs retained")
    return report


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--two-cycle", action="store_true")
    qualify(two_cycle=parser.parse_args().two_cycle)
