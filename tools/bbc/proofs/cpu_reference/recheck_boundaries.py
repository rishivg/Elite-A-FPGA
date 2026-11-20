"""Run fresh instruction SAT checks from sealed preparation, without regenerating RTL."""

from __future__ import annotations

import argparse
import json
import resource
import shutil
import subprocess
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from tools.bbc import cpu_instruction_proof, host_budget
from tools.bbc.proofs import memory_composition
from tools.bbc.proofs.video_cursor import hashes
from tools.validation.common import ROOT, digest, write_json

PROFILES = {
    "instruction": ("cpu-c-eligible-instruction-bridge", 17),
    "fetch": ("cpu-c-fetch-boundaries", 10),
}


def path(name):
    return Path(name) if Path(name).is_absolute() else ROOT / name


def collect(profiles, selected):
    """Check parent and child seals before accepting any prepared graph."""
    original = ROOT / "build/bbc/cpu-c-instruction-bridge/result.json"
    base = memory_composition.sealed(original)
    official = base["opcodes"]
    if len(official) != 151 or len(set(official)) != 151:
        raise ValueError("A complete original-source instruction premise is required")
    if selected is not None and (not selected or selected - {int(n, 16) for n in official}):
        raise ValueError("Select at least one documented opcode")
    files = {original, *map(path, base["inputs"]), *map(path, base["artifacts"])}
    cases = []
    for profile in profiles:
        name, depth = PROFILES[profile]
        receipt = ROOT / "build/bbc" / name / "result.json"
        parent = memory_composition.sealed(receipt)
        if not parent.get("complete_official_domain") or set(parent["opcodes"]) != set(official):
            raise ValueError("A complete source boundary premise is required")
        files.update([receipt, *map(path, parent["inputs"]), *map(path, parent["artifacts"])])
        for opcode in official:
            if selected is not None and int(opcode, 16) not in selected:
                continue
            child_path = receipt.parent / (opcode + ".json")
            child = json.loads(child_path.read_text())
            if child.get("passed") is not True or child.get("opcode") != opcode:
                raise ValueError("Incomplete source boundary: " + opcode)
            artifacts = {path(n): h for n, h in child["artifacts"].items()}
            if not artifacts or any(
                not p.is_file() or digest(p) != h for p, h in artifacts.items()
            ):
                raise ValueError("Changed source boundary artifact: " + opcode)
            graphs = [p for p in artifacts if p.name == "graph.json"]
            models = [p for p in artifacts if p.name == "source.v"]
            if len(graphs) != 1 or len(models) != 1:
                raise ValueError("Ambiguous source boundary preparation")
            graph, model = graphs[0], models[0]
            prefix = graph.parent.parent / "prefix.v"
            contract = (model.parent if profile == "instruction" else graph.parent) / "contract.v"
            if prefix not in artifacts or contract not in artifacts:
                raise ValueError("Unbound source boundary recipe")
            files.update(artifacts)
            cases.append(
                {
                    "name": profile + "-" + opcode,
                    "opcode": int(opcode, 16),
                    "depth": depth,
                    "graph": graph,
                    "model": model,
                    "contract": contract,
                    "prefix": prefix,
                }
            )
    return cases, files


def execute(case, work, *, reprepare=False):
    """Every invocation runs SAT; an old success is never the requested result."""
    directory = work / case["name"]
    directory.mkdir()
    graph = json.loads(case["graph"].read_text())
    if len(graph["modules"]) != 1:
        raise ValueError("Expected a flattened boundary graph")
    top = next(iter(graph["modules"]))
    cpu_instruction_proof.guard_assumptions(graph, top)
    preparation = f'read_json "{case["graph"]}"; '
    regenerated = directory / "graph.json"
    if reprepare:
        architecture = ROOT / "sim/formal/BbcCpuArchitectureContract.v"
        preparation = (
            f'read_verilog -formal -sv "{case["model"]}"; '
            f'read_verilog -formal "{case["contract"]}" "{case["prefix"]}" "{architecture}"; '
            f'prep -top {top} -flatten; check -assert; opt; chformal -lower; write_json "{regenerated}"; '
        )
    witness, log = directory / "witness.json", directory / "proof.log"
    query = (
        preparation
        + f"sat -set-assumes -set-init-zero -seq {case['depth']} -set opcode {case['opcode']} "
        + "-prove equivalent 1 -verify -timeout 120 -show equivalent -show step "
        + f'-dump_json "{witness}"'
    )
    started = time.monotonic()
    with log.open("w") as output:
        result = subprocess.run(
            ["yosys", "-Q", "-T", "-p", query],
            stdout=output,
            stderr=subprocess.STDOUT,
            timeout=300,
        )
    if reprepare:
        cpu_instruction_proof.guard_assumptions(json.loads(regenerated.read_text()), top)
    text = log.read_text()
    return {
        "name": case["name"],
        "opcode": f"{case['opcode']:02x}",
        "depth": case["depth"],
        "passed": result.returncode == 0
        and "SAT proof finished - no model found: SUCCESS!" in text,
        "exit_status": result.returncode,
        "seconds": time.monotonic() - started,
        "preparation_reused": not reprepare,
        "sat_executed": True,
        "artifacts": hashes([p for p in directory.iterdir() if p.is_file()]),
    }


def qualify(profiles=("instruction", "fetch"), selected=None, jobs=2, *, reprepare=False):
    if not profiles or set(profiles) - PROFILES.keys() or len(set(profiles)) != len(profiles):
        raise ValueError("Select distinct boundary profiles")
    if type(jobs) is not int or not 1 <= jobs <= 4:
        raise ValueError("Select one to four workers")
    cases, files = collect(profiles, selected)
    files.update(
        [
            Path(__file__),
            Path(cpu_instruction_proof.__file__),
            Path(memory_composition.__file__),
            Path(host_budget.__file__),
            ROOT / "sim/formal/BbcCpuArchitectureContract.v",
            Path(shutil.which("yosys")),
        ]
    )
    before = hashes(sorted(files))
    # Large graphs and logs belong on the scratch filesystem, not the checkout.
    work = Path(tempfile.mkdtemp(prefix="fpgaleet-c-boundary-recheck-"))
    started = time.monotonic()
    cpu = resource.getrusage(resource.RUSAGE_CHILDREN)
    results = []
    workers = min(jobs, host_budget.capacity())
    cases.sort(key=lambda c: (-c["graph"].stat().st_size, c["name"]))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        pending = [pool.submit(execute, c, work, reprepare=reprepare) for c in cases]
        for future in as_completed(pending):
            result = future.result()
            results.append(result)
            print(result["name"] + (": PASS" if result["passed"] else ": FAIL"), flush=True)
    consumed = resource.getrusage(resource.RUSAGE_CHILDREN)
    stable = before == hashes(sorted(files))
    report = {
        "passed": stable and len(results) == len(cases) and all(r["passed"] for r in results),
        "inputs_unchanged": stable,
        "inputs": before,
        "artifacts": {n: h for r in results for n, h in r["artifacts"].items()},
        "proofs": sorted(results, key=lambda r: r["name"]),
        "jobs": workers,
        "seconds": time.monotonic() - started,
        "cpu_seconds": consumed.ru_utime + consumed.ru_stime - cpu.ru_utime - cpu.ru_stime,
        "complete_official_domain": selected is None,
        "profiles": profiles,
        "preparation_reused": not reprepare,
        "scope": "Fresh initialized SAT rechecks of selected sealed original-source instruction and first-fetch graphs, at their unchanged 17/10-cycle depths. Complete parent inputs and selected child artifacts are hash checked before and after. Opcode-only domain guards are checked again. No previous SAT verdict is substituted, guest clocks shortened or expected outputs changed.",
        "limits": "Same bounded quiet source contracts and translation trust as the original receipts. Changed or missing preparation is rejected; regenerate with the original target. Fault controls, interrupts, arbitrary successors, warm reset and whole-machine/game equivalence remain separate.",
        "universal_game_equivalence": False,
    }
    write_json(work / "result.json", report)
    print(
        f"{len(results)} fresh SAT checks in {report['seconds']:.2f}s; {work}/result.json",
        flush=True,
    )
    if not report["passed"]:
        raise ValueError("Fresh source boundary recheck failed; retained logs and witnesses")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profiles", nargs="+", choices=PROFILES, default=list(PROFILES))
    parser.add_argument("--opcodes", nargs="+", type=lambda n: int(n, 16))
    parser.add_argument("--jobs", type=int, choices=range(1, 5), default=2)
    parser.add_argument(
        "--reprepare",
        action="store_true",
        help="Benchmark the original synthesis path before fresh SAT",
    )
    args = parser.parse_args()
    qualify(
        args.profiles,
        None if args.opcodes is None else set(args.opcodes),
        args.jobs,
        reprepare=args.reprepare,
    )
