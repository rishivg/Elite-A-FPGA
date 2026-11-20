"""Check NOP/BRK interrupt induction admission and real C source faults."""

import json
import shutil
import subprocess
from pathlib import Path

from tools.bbc import cpu_instruction_proof
from tools.bbc.proofs import branch_induction, memory_composition
from tools.bbc.proofs.cpu_reference import (
    frontend,
    instructions,
    prepared,
    prune,
    short_stream,
    structural,
)
from tools.bbc.proofs.video_cursor import hashes, replace_once
from tools.validation.common import ROOT, write_json


def admission(graph, work):
    top = "BbcCpuInterruptStreamContract"
    allowed = [0, 0xEA]
    excluded = sorted(set(range(256)) - set(allowed))
    commands = [
        f"read_json {graph}",
        f"hierarchy -top {top}",
        f"delete -output {top}/w:equivalent",
        f"setattr -unset keep {top}/w:*",
        "opt_clean -purge",
    ]
    commands += [
        f"sat -set-assumes -set-init-zero -seq 1 -set firstOpcode {a} -set nextOpcode {b} -show firstOpcode -show nextOpcode"
        for a in allowed
        for b in allowed
    ]
    commands += [
        f"sat -set-assumes -set-init-zero -seq 1 -set {slot} {op} -show {slot}"
        for slot in ("firstOpcode", "nextOpcode")
        for op in excluded
    ]
    script, log = work / "admission.ys", work / "admission.log"
    script.write_text("\n".join(commands) + "\n")
    with log.open("w") as output:
        subprocess.run(
            ["yosys", "-Q", "-T", "-s", str(script)],
            stdout=output,
            stderr=subprocess.STDOUT,
            check=True,
            timeout=180,
        )
    text = log.read_text()
    result = {
        "allowed_pairs": text.count("SAT solving finished - model found:"),
        "excluded_slot_checks": text.count("SAT solving finished - no model found."),
    }
    if result != {"allowed_pairs": 4, "excluded_slot_checks": 508}:
        raise ValueError("Incomplete protocol opcode admission")
    return result, [script, log]


def qualify():
    premise = ROOT / "build/bbc/cpu-c-protocol-history/result.json"
    memory_composition.sealed(premise)
    cold = ROOT / "build/bbc/cpu-c-cold-entry-profile/result.json"
    memory_composition.sealed(cold)
    source = ROOT / "build/bbc/reference-cycle/m6502.h"
    architecture = ROOT / "sim/formal/BbcCpuArchitectureContract.v"
    original = Path("/tmp/fpgaleet-c-protocol-invariants")
    prefix, contract, mapped = [original / n for n in ("prefix.v", "contract.v", "mapped.json")]
    work = Path("/tmp/fpgaleet-c-protocol-bridge")
    work.mkdir(exist_ok=True)
    directory = ROOT / "build/bbc/cpu-c-protocol-bridge"
    directory.mkdir(exist_ok=True)
    files = [
        Path(__file__),
        *[
            Path(m.__file__)
            for m in (
                frontend,
                instructions,
                prepared,
                prune,
                short_stream,
                structural,
                cpu_instruction_proof,
                branch_induction,
                memory_composition,
            )
        ],
        Path(__file__).parents[1] / "video_cursor.py",
        premise,
        cold,
        source,
        architecture,
        prefix,
        contract,
        mapped,
        Path(shutil.which("gcc")),
        Path(shutil.which("yosys")),
    ]
    before = hashes(files)
    domains, artifacts = admission(mapped, work)
    print("Protocol opcode admission: PASS", flush=True)
    faults = [
        (
            "lost-stack-decrement",
            "case (0x00<<3)|2: _SAD(0x0100|c->S--,c->PC);",
            "case (0x00<<3)|2: _SAD(0x0100|c->S,c->PC);",
        ),
        (
            "lost-nmi-hijack",
            "case (0x00<<3)|3: if(c->nmi_pip&&(0==(c->brk_flags&M6502_BRK_RESET)))",
            "case (0x00<<3)|3: if(0 && c->nmi_pip&&(0==(c->brk_flags&M6502_BRK_RESET)))",
        ),
        (
            "lost-handler-deferral",
            "if(c->nmi_pip){c->nmi_pip=0x100;} /* no interrupt poll at the end of the interrupt sequence */",
            "/* lost handler deferral */",
        ),
        ("wrong-hardware-status-b", "c->P &= ~M6502_BF;", "c->P |= M6502_BF;"),
    ]
    opcodes = sorted(set(cpu_instruction_proof.SCALAR_OPCODES) | {0x48, 0x28})
    if len(opcodes) != 44:
        raise ValueError("Changed sealed source decoder domain")
    results = []
    for name, old, new in faults:
        case = work / name
        case.mkdir(exist_ok=True)
        modified = case / "reference.h"
        modified.write_text(replace_once(source.read_text(), old, new))
        expanded = subprocess.check_output(
            ["gcc", "-E", "-P", "-"], input=frontend.extract(modified), text=True
        )
        model = case / "source.v"
        model.write_text(
            prune.prune(structural.generate(short_stream.adapter_ast(expanded, opcodes)))[0]
        )
        log, graph, optimized, witness = [
            case / n for n in ("proof.log", "graph.json", "mapped.json", "witness.json")
        ]
        witness.unlink(missing_ok=True)
        preparation = (
            f"read_verilog -formal -sv {model}; read_verilog -formal {prefix} {contract} {architecture}; "
            f"prep -top BbcCpuInterruptStreamContract -flatten; check -assert; opt; chformal -lower; write_json {graph}; "
            f"techmap; opt; abc -g AND; opt_clean; write_json {optimized}"
        )
        cached = prepared.prepare(
            case, preparation, [model, prefix, contract, architecture], [graph, optimized]
        )
        command = (
            f"read_json {optimized}; "
            f"sat -set-assumes -set-init-zero -seq 12 -prove equivalent 1 -verify -timeout 180 -show equivalent "
            f"-show firstOpcode -show nextOpcode -show irqPin -show nmiPin -dump_json {witness}"
        )
        with log.open("w") as output:
            run = subprocess.run(
                ["yosys", "-Q", "-T", "-p", command],
                stdout=output,
                stderr=subprocess.STDOUT,
                timeout=900,
            )
        branch_induction.guard_domains(
            json.loads(graph.read_text()), "BbcCpuInterruptStreamContract"
        )
        branch_induction.guard_domains(
            json.loads(optimized.read_text()), "BbcCpuInterruptStreamContract"
        )
        detected = (
            run.returncode != 0
            and "ERROR: Called with -verify and proof did fail!" in log.read_text()
            and witness.is_file()
            and any(
                s["name"] == "equivalent" and "0" in s.get("wave", "")
                for s in json.loads(witness.read_text())["signal"]
            )
        )
        artifacts += [
            modified,
            model,
            log,
            graph,
            optimized,
            *([witness] if witness.exists() else []),
        ]
        results.append({"name": name, "detected": detected, "preparation_cache_reused": cached})
        print(name + (": PASS" if detected else ": FAIL"), flush=True)
        if not detected:
            break
    stable = before == hashes(files)
    passed = stable and len(results) == 4 and all(r["detected"] for r in results)
    memory_composition.sealed(premise)
    write_json(
        directory / "result.json",
        {
            "passed": passed,
            "inputs_unchanged": stable,
            "inputs": before,
            "artifacts": hashes(artifacts),
            "opcode_admission": domains,
            "faults": results,
            "scope": "Sealed initialized NOP/BRK source history induction with arbitrary IRQ/NMI and vector/response bytes, all four allowed opcode pairs admitted and 508 excluded-slot checks. Four actual C mutations must yield initialized counterexamples: stack decrement, NMI hijack, first-handler deferral and hardware status B. No faults in property or observation logic; source variants are regenerated from the actual maintained header.",
            "cold_entry_connection": "The sealed real cold-entry profile supplies architecture, fetch PC/RW/SYNC, fixed quiet interrupt/BCD/6510/status fields and zero previous NMI. The unit permits that previous-NMI reset sample and leaves other prior temporaries arbitrary; observer history before first execution is ignored. This connects the declared register/PLP prefix, not real OS startup or global clock phase.",
            "limits": "NOP/BRK instruction domain and quiet reference entry. Complete source decoder partition, arithmetic/branch/memory/control successors, custom C translation, IRQ/NMI during physical waits, warm reset, original OS entry and whole-machine/game composition remain open.",
            "universal_game_equivalence": False,
        },
    )
    if not passed:
        raise ValueError("Source interrupt protocol bridge incomplete; see retained logs")


if __name__ == "__main__":
    qualify()
