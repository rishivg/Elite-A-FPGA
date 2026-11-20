"""Check the admitted memory domain and detect faults in actual C transitions."""

import json
import shutil
import subprocess
from pathlib import Path

import pycparser

from tools.bbc.proofs import branch_induction, memory_composition
from tools.bbc.proofs.cpu_reference import (
    documented_model,
    frontend,
    prepared,
    prune,
    short_stream,
    structural,
)
from tools.bbc.proofs.video_cursor import hashes, replace_once
from tools.validation.common import ROOT, digest, write_json

TOP = "BbcCpuInterruptStreamContract"
CONTROLS = {0x08, 0x48, 0x28, 0x68, 0x4C, 0x6C, 0x20, 0x60, 0x40}
FAULTS = (
    ("wrong-zero-x-address", 0x15, "_SA((c->AD+c->X)&0x00FF);", "_SA(c->AD);", 2),
    ("wrong-zero-y-register", 0xB6, "c->AD+c->Y", "c->AD+c->X", 2),
    ("wrong-absolute-read", 0x0D, "c->A|=_GD();", "c->A&=_GD();", 3),
    ("wrong-absolute-x-address", 0x1D, "_SA(c->AD+c->X);", "_SA(c->AD);", 3),
    ("wrong-absolute-y-address", 0x19, "_SA(c->AD+c->Y);", "_SA(c->AD);", 3),
    ("wrong-indirect-x-wrap", 0x01, "_SA((c->AD+1)&0xFF);", "_SA(c->AD+1);", 3),
    ("wrong-indirect-y-store", 0x91, "_SD(c->A);", "_SD(c->X);", 4),
    ("lost-indexed-rmw-write", 0x16, "c->AD=_GD();_WR();", "c->AD=_GD();", 3),
)


def admission(graph, work, allowed):
    """Prove the actual sole assumption equals the independent inventory.

    A combinational SAT query covers all 65,536 pairs, including exclusions,
    without executing thousands of separately specialized SAT commands.
    The property is tested without applying the assumption being checked.
    """
    design = json.loads(graph.read_text())
    branch_induction.guard_domains(design, TOP)
    module = design["modules"][TOP]
    assumptions = [c for c in module["cells"].values() if c["type"] == "$assume"]
    bits = assumptions[0]["connections"]["A"]
    ports = {n: module["netnames"][n]["bits"] for n in ("firstOpcode", "nextOpcode")}
    module["ports"] = {
        **{n: {"direction": "input", "bits": b} for n, b in ports.items()},
        "domain": {"direction": "output", "bits": bits},
    }
    module["cells"] = {
        n: c
        for n, c in module["cells"].items()
        if c["type"] != "$assume"
        and not (c["type"] in ("$anyseq", "$anyconst") and c["connections"]["Y"] in ports.values())
    }
    for net in module["netnames"].values():
        net["attributes"].pop("keep", None)
    domain_graph = work / "admission.json"
    domain_graph.write_text(json.dumps(design))
    wrapper = work / "admission.v"
    wrapper.write_text(
        "module Admission(input [7:0] firstOpcode,nextOpcode,output equivalent);\n"
        + documented_model.context_function(allowed)
        + f"wire domain; {TOP} actual(.firstOpcode(firstOpcode),.nextOpcode(nextOpcode),.domain(domain));\n"
        + "assign equivalent=domain==(documented(firstOpcode)&&documented(nextOpcode));\nendmodule\n"
    )
    log = work / "admission.log"
    query = f"read_json {domain_graph}; hierarchy -top {TOP}; opt_clean -purge; read_verilog {wrapper}; prep -top Admission -flatten; memory_map; opt; check -assert; sat -prove equivalent 1 -verify -timeout 60"
    with log.open("w") as output:
        trial = subprocess.run(
            ["yosys", "-Q", "-T", "-p", query],
            stdout=output,
            stderr=subprocess.STDOUT,
            timeout=180,
        )
    if trial.returncode or "SAT proof finished - no model found: SUCCESS!" not in log.read_text():
        raise ValueError("Actual opcode assumption differs from independent inventory")
    return {"all_pairs": 65536, "allowed_pairs": len(allowed) ** 2, "assumptions_applied": False}, [
        domain_graph,
        wrapper,
        log,
    ]


def qualify():
    premises = [
        ROOT / "build/bbc" / n / "result.json"
        for n in ("cpu-c-documented-memory-investigation", "cpu-c-structural-adapter")
    ]
    history, original = [memory_composition.sealed(p) for p in premises]
    positive = Path("/tmp/fpgaleet-c-documented-memory")
    contract, mapped, prefix = [positive / n for n in ("contract.v", "mapped.json", "prefix.v")]
    for path in (contract, mapped, prefix):
        if history["artifacts"].get(str(path)) != digest(path):
            raise ValueError("Changed positive memory history")
    source = ROOT / "build/bbc/reference-cycle/m6502.h"
    if original["inputs"].get(str(source.relative_to(ROOT))) != digest(source):
        raise ValueError("Changed actual C source")
    architecture = ROOT / "sim/formal/BbcCpuArchitectureContract.v"
    work = Path("/tmp/fpgaleet-c-memory-interrupt-checks")
    work.mkdir(exist_ok=True)
    inputs = [
        Path(__file__),
        *[
            Path(m.__file__)
            for m in (
                documented_model,
                frontend,
                prepared,
                prune,
                short_stream,
                structural,
                branch_induction,
                memory_composition,
            )
        ],
        Path(__file__).parents[1] / "video_cursor.py",
        ROOT / "tools/bbc/reference/official_stimulus.h",
        *premises,
        source,
        contract,
        mapped,
        prefix,
        architecture,
        Path(shutil.which("gcc")),
        Path(shutil.which("yosys")),
        *sorted(Path(pycparser.__file__).parent.rglob("*.py")),
    ]
    before = hashes(inputs)
    allowed = sorted(set(documented_model.inventory()) - CONTROLS)
    if len(allowed) != 142:
        raise ValueError("Changed memory opcode inventory")
    domain, artifacts = admission(mapped, work, allowed)
    print("All 65536 domain pairings: PASS", flush=True)
    faults = []
    for name, opcode, old, new, step in FAULTS:
        case = work / name
        case.mkdir(exist_ok=True)
        header, model = case / "reference.h", case / "source.v"
        anchor = f"case (0x{opcode:02X}<<3)|{step}: "
        line = next(line for line in source.read_text().splitlines() if anchor in line)
        changed = replace_once(line, old, new)
        header.write_text(replace_once(source.read_text(), line, changed))
        expanded = subprocess.check_output(
            ["gcc", "-E", "-P", "-"], input=frontend.extract(header), text=True
        )
        model.write_text(
            prune.prune(
                structural.generate(short_stream.adapter_ast(expanded, allowed + [0x48, 0x28]))
            )[0]
        )
        graph, optimized, log, witness = [
            case / n for n in ("graph.json", "mapped.json", "proof.log", "witness.json")
        ]
        witness.unlink(missing_ok=True)
        command = f"read_verilog -formal -sv {model}; read_verilog -formal {prefix} {contract} {architecture}; prep -top {TOP} -flatten; check -assert; opt; memory_map; opt; chformal -lower; write_json {graph}; techmap; opt; abc -g AND; opt_clean; write_json {optimized}"
        cached = prepared.prepare(
            case, command, [model, prefix, contract, architecture], [graph, optimized], timeout=1800
        )
        stimulus = f"-set firstOpcode {opcode} -set nextOpcode 234 -set irqPin 0 -set nmiPin 0"
        query = f"read_json {optimized}; sat -set-assumes -set-init-zero -seq 14 -prove observedEquivalent 1 -verify {stimulus} -timeout 300 -show observedEquivalent -show firstOpcode -show nextOpcode -dump_json {witness}"
        with log.open("w") as output:
            trial = subprocess.run(
                ["yosys", "-Q", "-T", "-p", query],
                stdout=output,
                stderr=subprocess.STDOUT,
                timeout=900,
            )
        for path in (graph, optimized):
            branch_induction.guard_domains(json.loads(path.read_text()), TOP)
        detected = (
            trial.returncode != 0
            and "ERROR: Called with -verify and proof did fail!" in log.read_text()
            and witness.is_file()
            and any(
                s["name"] == "observedEquivalent" and "0" in s.get("wave", "")
                for s in json.loads(witness.read_text())["signal"]
            )
        )
        artifacts += [
            header,
            model,
            graph,
            optimized,
            log,
            case / "prepared.json",
            case / "prepare.log",
            *([witness] if witness.exists() else []),
        ]
        faults.append(
            {
                "name": name,
                "detected": detected,
                "preparation_cache_reused": cached,
                "negative_query_stimulus": stimulus,
            }
        )
        print(name + (": PASS" if detected else ": FAIL"), flush=True)
        if not detected:
            break
    stable = before == hashes(inputs)
    passed = stable and len(faults) == len(FAULTS) and all(f["detected"] for f in faults)
    write_json(
        ROOT / "build/bbc/cpu-c-memory-interrupt-bridge/result.json",
        {
            "passed": passed,
            "inputs_unchanged": stable,
            "inputs": before,
            "artifacts": hashes(artifacts),
            "opcode_admission": domain,
            "opcodes": allowed,
            "faults": faults,
            "scope": "Initialized arbitrary-IRQ/NMI source history induction for 142 official opcodes. Exact dispatch context is a conclusion. Fresh universal combinational check of the sole opcode assumption over all pairs without applying that assumption. Eight actual C mutations must yield initialized observable bus/architecture counterexamples; only negative queries specialize opcodes and interrupts.",
            "limits": "Quiet abstract fetch entry; nine remaining control instructions, OS reachability, enable/warm histories and global device/video scheduling remain open.",
            "universal_game_equivalence": False,
        },
    )
    if not passed:
        raise ValueError("Source memory interrupt controls incomplete")


if __name__ == "__main__":
    qualify()
