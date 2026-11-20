"""Check admission and real source faults for the branch interrupt bridge."""

import json
import shutil
import subprocess
from pathlib import Path

from tools.bbc import cpu_instruction_proof
from tools.bbc.proofs import branch_induction, memory_composition
from tools.bbc.proofs.cpu_reference import frontend, prepared, prune, short_stream, structural
from tools.bbc.proofs.video_cursor import hashes, replace_once
from tools.validation.common import ROOT, digest, write_json


def admission(graph, work, allowed):
    top = "BbcCpuInterruptStreamContract"
    excluded = sorted(set(range(256)) - set(allowed))
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
        for a in allowed
        for b in allowed
    ]
    commands += [
        f"sat -set-assumes -set-init-zero -seq 1 -set {slot} {opcode} -show {slot}"
        for slot in ("firstOpcode", "nextOpcode")
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
    result = {
        "allowed_pairs": text.count("SAT solving finished - model found:"),
        "excluded_slot_checks": text.count("SAT solving finished - no model found."),
    }
    if result != {"allowed_pairs": len(allowed) ** 2, "excluded_slot_checks": len(excluded) * 2}:
        raise ValueError("Incomplete branch interrupt admission")
    return result, [script, log]


def qualify():
    paths = [
        ROOT / "build/bbc" / n / "result.json"
        for n in (
            "cpu-c-branch-interrupts-investigation",
            "cpu-c-protocol-bridge",
            "cpu-c-quiet-stream-bridge",
        )
    ]
    history, protocol, quiet = [memory_composition.sealed(p) for p in paths]
    old = Path("/tmp/fpgaleet-c-branch-interrupts")
    prefix = Path("/tmp/fpgaleet-c-protocol-invariants/prefix.v")
    contract, mapped = old / "contract.v", old / "mapped.json"
    architecture = ROOT / "sim/formal/BbcCpuArchitectureContract.v"
    models = [
        (
            record["name"],
            Path("/tmp/fpgaleet-c-protocol-bridge") / record["name"] / "source.v",
            protocol,
        )
        for record in protocol["faults"]
    ]
    models += [
        (n, Path("/tmp/fpgaleet-c-quiet-stream") / n / "source.v", quiet)
        for n in ("wrong-carry", "lost-decimal-mode", "lost-branch-page-target")
    ]
    for _, model, bound in models:
        if bound["artifacts"].get(str(model)) != digest(model):
            raise ValueError("Changed actual source fault model")
    for path in (contract, mapped):
        if history["artifacts"].get(str(path)) != digest(path):
            raise ValueError("Changed bound branch property")
    work = Path("/tmp/fpgaleet-c-branch-interrupt-checks")
    work.mkdir(exist_ok=True)
    directory = ROOT / "build/bbc/cpu-c-branch-interrupt-bridge"
    directory.mkdir(exist_ok=True)
    files = [
        Path(__file__),
        Path(prepared.__file__),
        Path(branch_induction.__file__),
        Path(memory_composition.__file__),
        Path(cpu_instruction_proof.__file__),
        Path(__file__).parents[1] / "video_cursor.py",
        *paths,
        prefix,
        contract,
        mapped,
        architecture,
        *[m for _, m, _ in models],
        Path(shutil.which("yosys")),
    ]
    source = ROOT / "build/bbc/reference-cycle/m6502.h"
    if quiet["inputs"].get(str(source.relative_to(ROOT))) != digest(source):
        raise ValueError("Changed original branch C source")
    files += [
        source,
        Path(shutil.which("gcc")),
        *[Path(m.__file__) for m in (frontend, prune, short_stream, structural)],
    ]
    before = hashes(files)
    fault = work / "lost-branch-irq-restoration"
    fault.mkdir(exist_ok=True)
    modified = fault / "reference.h"
    modified.write_text(
        replace_once(
            source.read_text(),
            "case (0x10<<3)|3: c->PC=c->AD;if(c->branch_irq)c->irq_pip|=0x200;_FETCH();break;",
            "case (0x10<<3)|3: c->PC=c->AD;_FETCH();break;",
        )
    )
    expanded = subprocess.check_output(
        ["gcc", "-E", "-P", "-"], input=frontend.extract(modified), text=True
    )
    model = fault / "source.v"
    model.write_text(
        prune.prune(
            structural.generate(
                short_stream.adapter_ast(
                    expanded, sorted(set(cpu_instruction_proof.SCALAR_OPCODES) | {0x48, 0x28})
                )
            )
        )[0]
    )
    models.append(("lost-branch-irq-restoration", model, None))
    opcodes = sorted(set(cpu_instruction_proof.SCALAR_OPCODES))
    if len(opcodes) != 42:
        raise ValueError("Changed short/branch/BRK opcode inventory")
    domains, artifacts = admission(mapped, work, opcodes)
    artifacts += [modified, model]
    print("Branch IRQ/NMI admission: PASS", flush=True)
    results = []
    for name, model, _ in models:
        case = work / name
        case.mkdir(exist_ok=True)
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
        # Use a concrete valid page-crossing BPL/NOP sequence for this fault.
        # Only the negative query is specialized; positive induction and opcode
        # admission still allow every input in their original domain.
        stimulus = (
            "-set firstOpcode 16 -set nextOpcode 234 -set p 0 -set base 224 -set nextOperand 64 -set nmiPin 0 "
            if name == "lost-branch-irq-restoration"
            else ""
        )
        query = (
            f"read_json {optimized}; sat -set-assumes -set-init-zero -seq 12 -prove observedEquivalent 1 -verify "
            f"{stimulus}-timeout 180 -show equivalent -show observedEquivalent -show firstOpcode -show nextOpcode -show irqPin -show nmiPin -dump_json {witness}"
        )
        with log.open("w") as output:
            run = subprocess.run(
                ["yosys", "-Q", "-T", "-p", query],
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
                s["name"] == "observedEquivalent" and "0" in s.get("wave", "")
                for s in json.loads(witness.read_text())["signal"]
            )
        )
        artifacts += [
            log,
            graph,
            optimized,
            case / "prepared.json",
            case / "prepare.log",
            *([witness] if witness.exists() else []),
        ]
        results.append(
            {
                "name": name,
                "detected": detected,
                "preparation_cache_reused": cached,
                "query_input_specialization": stimulus.strip(),
            }
        )
        print(name + (": PASS" if detected else ": FAIL"), flush=True)
        if not detected:
            break
    stable = before == hashes(files)
    passed = stable and len(results) == 8 and all(r["detected"] for r in results)
    write_json(
        directory / "result.json",
        {
            "passed": passed,
            "inputs_unchanged": stable,
            "inputs": before,
            "artifacts": hashes(artifacts),
            "opcode_admission": domains,
            "faults": results,
            "opcodes": opcodes,
            "scope": "Sealed initialized source history induction for 33 two-cycle instructions, all eight branches and BRK, with arbitrary IRQ/NMI and response/vector bytes. Every allowed opcode pairing is admitted, both slots exclude all other opcodes. Eight actual C source mutations must each yield an initialized counterexample; preparation caching never reuses SAT results.",
            "limits": "Quiet reference fetch entry and specialized source model. Complete decoder, memory/control successors, custom C translation, real OS/global phase, physical enables/warm reset and whole-game equivalence remain separate.",
            "universal_game_equivalence": False,
        },
    )
    if not passed:
        raise ValueError("Source branch interrupt bridge incomplete")


if __name__ == "__main__":
    qualify()
