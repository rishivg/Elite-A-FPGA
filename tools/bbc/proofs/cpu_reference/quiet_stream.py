"""Prove quiet C short/branch streams, admission and actual source faults."""

import json
import shutil
import subprocess
from pathlib import Path

import pycparser

from tools.bbc import cpu_instruction_proof
from tools.bbc.proofs import branch_induction, memory_composition
from tools.bbc.proofs.cpu_reference import (
    eligible_stream,
    frontend,
    instructions,
    prune,
    short_stream,
    stream_invariants,
    structural,
)
from tools.bbc.proofs.video_cursor import hashes, replace_once
from tools.validation.common import ROOT, write_json


def observed_prefix(initial, pins, original):
    return replace_once(
        replace_once(
            eligible_stream.prefix(initial, pins, original),
            "output [255:0] proofState, output [63:0] proofPins, output proofTrap",
            "output [255:0] proofState, output [63:0] proofPins, output proofTrap, output [255:0] proofHistory, output proofPreviousFetch",
        ),
        "endmodule",
        "assign proofHistory=retirementState;assign proofPreviousFetch=previousFetch;\nendmodule",
    )


def admission(graph, work, opcodes):
    top = "BbcCpuBranchStreamContract"
    script, log = work / "admission.ys", work / "admission.log"
    commands = [
        f"read_json {graph}",
        f"hierarchy -top {top}",
        f"delete -output {top}/w:equivalent",
        f"setattr -unset keep {top}/w:*",
        "opt_clean -purge",
    ]
    commands += [
        f"sat -set-assumes -set-init-zero -seq 1 -set firstOpcode {a} -set nextOpcode {b} -show firstOpcode -show nextOpcode"
        for a in opcodes
        for b in opcodes
    ]
    excluded = sorted(set(range(256)) - set(opcodes))
    commands += [
        f"sat -set-assumes -set-init-zero -seq 1 -set {signal} {opcode} -show {signal}"
        for signal in ("firstOpcode", "nextOpcode")
        for opcode in excluded
    ]
    script.write_text("\n".join(commands) + "\n")
    with log.open("w") as output:
        subprocess.run(
            ["yosys", "-Q", "-T", "-s", str(script)],
            stdout=output,
            stderr=subprocess.STDOUT,
            check=True,
            timeout=300,
        )
    text = log.read_text()
    admitted = text.count("SAT solving finished - model found:")
    rejected = text.count("SAT solving finished - no model found.")
    if admitted != len(opcodes) ** 2 or rejected != 2 * len(excluded):
        raise ValueError("Incomplete quiet-stream opcode admission")
    return {"allowed_pairs": admitted, "excluded_slot_checks": rejected}, [script, log]


def qualify():
    cold = ROOT / "build/bbc/cpu-c-cold-entry-bridge/result.json"
    initialized = memory_composition.sealed(cold)
    profile = ROOT / "build/bbc/cpu-c-cold-entry-profile/result.json"
    premises = [
        cold,
        profile,
        ROOT / "build/bbc/cpu-c-structural-adapter/result.json",
        ROOT / "build/bbc/cpu-c-structural-pruning/result.json",
    ]
    for path in premises:
        memory_composition.sealed(path)
    source = ROOT / "build/bbc/reference-cycle/m6502.h"
    expanded = ROOT / "build/bbc/cpu-c-reference-transpiler/expanded.c"
    original_prefix = ROOT / "sim/formal/BbcCpuStreamPrefix.v"
    original_contract = ROOT / "sim/formal/BbcCpuBranchStreamContract.v"
    architecture = ROOT / "sim/formal/BbcCpuArchitectureContract.v"
    opcodes = sorted(set(cpu_instruction_proof.SCALAR_OPCODES) - {0})
    if len(opcodes) != 41:
        raise ValueError("Changed stream domain")
    work = Path("/tmp/fpgaleet-c-quiet-stream")
    work.mkdir(exist_ok=True)
    directory = ROOT / "build/bbc/cpu-c-quiet-stream-bridge"
    directory.mkdir(exist_ok=True)
    files = [
        Path(__file__),
        *[
            Path(module.__file__)
            for module in (
                branch_induction,
                memory_composition,
                eligible_stream,
                frontend,
                instructions,
                prune,
                short_stream,
                stream_invariants,
                structural,
                cpu_instruction_proof,
            )
        ],
        Path(__file__).parents[1] / "video_cursor.py",
        *premises,
        source,
        expanded,
        original_prefix,
        original_contract,
        architecture,
        Path(shutil.which("yosys")),
        Path(shutil.which("gcc")),
        *sorted(Path(pycparser.__file__).parent.rglob("*.py")),
    ]
    before = hashes(files)
    prefix, contract = work / "prefix.v", work / "contract.v"
    prefix.write_text(
        observed_prefix(
            initialized["default_native_state"],
            initialized["default_native_pins"],
            original_prefix.read_text(),
        )
    )
    contract.write_text(stream_invariants.strengthen(original_contract.read_text()))
    variants = [("positive", expanded.read_text())]
    source_faults = [
        (
            "wrong-carry",
            "case (0x18<<3)|1: c->P&=~0x1;_FETCH();break;",
            "case (0x18<<3)|1: c->P|=0x1;_FETCH();break;",
        ),
        (
            "lost-decimal-mode",
            "case (0xF8<<3)|1: c->P|=0x8;_FETCH();break;",
            "case (0xF8<<3)|1: c->P&=~0x8;_FETCH();break;",
        ),
        (
            "lost-branch-page-target",
            "case (0x10<<3)|3: c->PC=c->AD;if(c->branch_irq)c->irq_pip|=0x200;_FETCH();break;",
            "case (0x10<<3)|3: c->PC=c->AD+1;if(c->branch_irq)c->irq_pip|=0x200;_FETCH();break;",
        ),
    ]
    artifacts = [prefix, contract]
    for name, old, new in source_faults:
        reference = work / (name + ".h")
        reference.write_text(replace_once(source.read_text(), old, new))
        text = subprocess.check_output(
            ["gcc", "-E", "-P", "-"], input=frontend.extract(reference), text=True
        )
        variants.append((name, text))
        artifacts.append(reference)
    checks = []
    domains = None
    for name, text in variants:
        case = work / name
        case.mkdir(exist_ok=True)
        rtl = case / "source.v"
        rtl.write_text(
            prune.prune(
                structural.generate(short_stream.adapter_ast(text, opcodes + [0, 0x48, 0x28]))
            )[0]
        )
        log, graph, mapped, witness = [
            case / n for n in ("proof.log", "graph.json", "mapped.json", "witness.json")
        ]
        witness.unlink(missing_ok=True)
        mode = (
            "-tempinduct -initsteps 2 -maxsteps 8 -stepsize 1" if name == "positive" else "-seq 8"
        )
        command = (
            f"read_verilog -formal -sv {rtl}; read_verilog -formal {prefix} {contract} {architecture}; "
            f"prep -top BbcCpuBranchStreamContract -flatten; check -assert; opt; chformal -lower; write_json {graph}; "
            f"techmap; opt; abc -g AND; opt_clean; write_json {mapped}; "
            f"sat -set-assumes -set-init-zero {mode} -prove equivalent 1 -verify -timeout 120 "
            f"-show equivalent -show firstOpcode -show nextOpcode -dump_json {witness}"
        )
        with log.open("w") as output:
            run = subprocess.run(
                ["yosys", "-Q", "-T", "-p", command],
                stdout=output,
                stderr=subprocess.STDOUT,
                timeout=600,
            )
        branch_induction.guard_domains(json.loads(graph.read_text()))
        branch_induction.guard_domains(json.loads(mapped.read_text()))
        accepted = (
            run.returncode == 0 and "Induction step proven: SUCCESS!" in log.read_text()
            if name == "positive"
            else run.returncode != 0
            and "ERROR: Called with -verify and proof did fail!" in log.read_text()
            and witness.is_file()
            and any(
                s["name"] == "equivalent" and "0" in s.get("wave", "")
                for s in json.loads(witness.read_text())["signal"]
            )
        )
        artifacts.extend([rtl, log, graph, mapped, *([witness] if witness.exists() else [])])
        checks.append({"name": name, "accepted": accepted})
        print(name + (": PASS" if accepted else ": FAIL"), flush=True)
        if not accepted:
            break
        if name == "positive":
            domains, extra = admission(mapped, work, opcodes)
            artifacts.extend(extra)
            print("Opcode admission: PASS", flush=True)
    stable = before == hashes(files)
    passed = (
        stable and domains is not None and len(checks) == 4 and all(c["accepted"] for c in checks)
    )
    write_json(
        directory / "result.json",
        {
            "passed": passed,
            "inputs_unchanged": stable,
            "inputs": before,
            "artifacts": hashes(artifacts),
            "proofs": checks,
            "opcode_admission": domains,
            "opcodes": opcodes,
            "scope": "Unbounded quiet source-derived streams of 33 two-cycle operations and eight conditional branches, all meaningful architectural state, PCs and operand bytes symbolic. Arbitrary prior IR/AD/PINS/branch latch at a quiet opcode-fetch boundary. Original bus/architecture contract retained and strengthened with proved read-only retirement/decoder state relations. Sole assumption is the two-opcode domain; every allowed pairing and excluded opcode is checked. Three actual source faults must yield initialized counterexamples.",
            "cold_entry_connection": "The real C cold-entry premise establishes architecture and fetch pins; its strengthened profile premise establishes every fixed quiet/BCD/6510/status field. Other prior C fields are arbitrary here. Read-only observer reset initializes the specification, not actual FPGA state. This connects the declared register initialization prefix; it does not prove the original OS entry or global phase.",
            "limits": "C and structural translation remain explicit trust boundaries. Full decoder specialization, memory/control successor streams, IRQ/NMI, warm reset, arbitrary enables, real OS startup, guest reachability and whole-machine reference/event/video composition remain separate. No universal game equivalence.",
            "universal_game_equivalence": False,
        },
    )
    if not passed:
        raise ValueError("Quiet source stream bridge incomplete")
    return passed


if __name__ == "__main__":
    qualify()
