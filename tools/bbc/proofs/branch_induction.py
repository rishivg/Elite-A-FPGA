"""Prove CPU streams and audit the first and subsequent opcode domains."""

import json
import subprocess
from pathlib import Path

from tools.bbc import build, cpu_instruction_proof
from tools.validation.common import ROOT, digest, write_json

TOP = "BbcCpuBranchStreamContract"


def guard_domains(graph, top=TOP):
    """Ensure the sole assumption restricts only the first and subsequent symbolic opcodes."""
    module = graph["modules"][top]
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


def qualify(
    *,
    contract="BbcCpuBranchStreamContract",
    opcodes=None,
    name="cpu-branch-induction-proof",
    initial_steps=44,
    max_steps=60,
    extra_faults=(),
    extra_inputs=(),
    scope_extension="",
    host_timeout=900,
):
    """Run full initialized induction; reduce only the separate opcode admission graph."""
    if not build.generation_current():
        raise ValueError("Run make bbc-gen before proving current CPU RTL")
    directory = build.BBC / name
    opcodes = (
        tuple(op for op in cpu_instruction_proof.SCALAR_OPCODES if op != 0)
        if opcodes is None
        else tuple(opcodes)
    )
    top = contract
    directory.mkdir(parents=True, exist_ok=True)
    core = build.BBC / "ArletCpuEnabled.v"
    formal = ROOT / "sim/formal"
    common = [
        ROOT / "rtl/bbc/BbcCpu.v",
        ROOT / "third_party/6502/ALU.v",
        formal / "BbcCpuStreamPrefix.v",
        formal / "BbcCpuArchitectureContract.v",
        formal / (contract + ".v"),
    ]
    inputs = [Path(__file__), core, *common, *extra_inputs]
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
    branch_fault = directory / "inverted-bpl.v"
    branch_target = "3'b000: cond_true = ~N;"
    if actual.count(branch_target) != 1:
        raise ValueError("Missing branch predicate negative-control target")
    branch_fault.write_text(actual.replace(branch_target, "3'b000: cond_true = N;"))
    results, artifacts = [], [fault, branch_fault]
    graph = directory / "positive.json"
    variants = [("positive", core), ("old-decimal", fault), ("inverted-bpl", branch_fault)]
    for label, correct, wrong in extra_faults:
        if actual.count(correct) != 1:
            raise ValueError("Missing actual RTL mutation target: " + label)
        path = directory / (label + ".v")
        path.write_text(actual.replace(correct, wrong))
        artifacts.append(path)
        variants.append((label, path))
    for name, model in variants:
        log = directory / (name + ".log")
        command = (
            "read_verilog -formal "
            + " ".join(map(str, [model, *common]))
            + f"; prep -top {top} -flatten; memory_map; opt; async2sync; opt_clean; "
            + (f"write_json {graph}; " if name == "positive" else "")
            + f"sat -set-assumes -set-init-zero -tempinduct -initsteps {initial_steps} -maxsteps {max_steps} -stepsize 4 -prove equivalent 1 -verify -timeout 300"
        )
        with log.open("w") as output:
            run = subprocess.run(
                ["yosys", "-Q", "-T", "-p", command],
                cwd=ROOT,
                stdout=output,
                stderr=subprocess.STDOUT,
                timeout=host_timeout,
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
    guard_domains(json.loads(graph.read_text()), top)
    commands = "; ".join(
        f"sat -set-assumes -set-init-zero -seq 1 -set firstOpcode {a} -set nextOpcode {b} -show firstOpcode -show nextOpcode"
        for a in opcodes
        for b in opcodes
    )
    excluded = tuple(op for op in range(256) if op not in opcodes)
    commands += "; " + "; ".join(
        f"sat -set-assumes -set-init-zero -seq 1 -set {signal} {opcode} -show {signal}"
        for signal in ("firstOpcode", "nextOpcode")
        for opcode in excluded
    )
    domain_graph = directory / "domains.json"
    domains_script = directory / "domains.ys"
    domains_script.write_text(
        f"read_json {graph}\nhierarchy -top {top}\ndelete -output {top}/w:equivalent\nopt_clean\nwrite_json {domain_graph}\n"
        + commands.replace("; ", "\n")
        + "\n"
    )
    domains = directory / "domains.log"
    with domains.open("w") as output:
        run = subprocess.run(
            ["yosys", "-Q", "-T", "-s", str(domains_script)],
            cwd=ROOT,
            stdout=output,
            stderr=subprocess.STDOUT,
            timeout=host_timeout,
        )
    guard_domains(json.loads(domain_graph.read_text()), top)
    admitted = (
        run.returncode == 0
        and domains.read_text().count("SAT solving finished - model found:") == len(opcodes) ** 2
        and domains.read_text().count("SAT solving finished - no model found.") == len(excluded) * 2
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
        "induction": f"Initialized base cases plus temporal induction, depths {initial_steps} through {max_steps} by four",
        "independent_support": [str(p.relative_to(ROOT)) for p in support],
        "tools": {"yosys": subprocess.check_output(["yosys", "-V"], text=True).strip()},
        "artifacts": build.hashes([*artifacts, graph, domain_graph, domains, domains_script]),
        "contract_sha256": digest(common[-1]),
        "scope": (
            f"Arbitrary-length streams of {len(opcodes)} declared opcodes after eight reset edges "
            "and the ordinary register/stack/PLP prefix. A/X/Y/S/P and reset PC are symbolic. "
            "Subsequent opcode and operand responses may change at each read. The full CPU and "
            "independent NMOS model compare bus requests, write values, retirement PCs and "
            "architectural registers and flags. The single unconditional assumption restricts "
            f"only the opcode sources. All {len(opcodes) ** 2:,} admitted first/next pairs are "
            f"satisfiable and all {len(excluded) * 2} excluded opcode positions are unsatisfiable. "
            "The full proof includes initialized base cases, induction and actual RTL faults. "
            "Independent arithmetic and documented-opcode results at periods 1/16/48 are sealed. "
            "Branch predicates, offsets, page-crossing reads and CLI/SEI flag timing are checked. "
            + scope_extension
        ).rstrip(),
        "limits": (
            "IRQ and NMI are inactive, enable period is one, and power-on state is zero. "
            "The initialization prefix is fixed. Synthetic read responses may differ from a "
            "static memory image. Parent-machine composition requires separate evidence."
        ),
        "universal_cpu_equivalence": False,
        "universal_game_equivalence": False,
        "domain_admission": (
            "The admission graph drops only the architectural output and dead cells. "
            "Both graphs are checked for the same unrestricted opcode-only assumption. "
            "Induction and mutation checks run on the full CPU."
        ),
        "excluded_opcode_positions": len(excluded) * 2,
    }
    write_json(directory / "result.json", report)
    if not report["passed"]:
        raise ValueError("Immediate-stream induction failed; all RTL and logs retained")
    return report


if __name__ == "__main__":
    qualify()
