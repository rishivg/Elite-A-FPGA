"""Qualify full official histories with defined-execution C control faults."""

import json
import shutil
import subprocess
from pathlib import Path

import pycparser

from tools.bbc.proofs import branch_induction, memory_composition
from tools.bbc.proofs.cpu_reference import (
    documented_model,
    frontend,
    memory_interrupt_checks,
    prepared,
    prune,
    short_stream,
    structural,
)
from tools.bbc.proofs.video_cursor import hashes, replace_once
from tools.validation.common import ROOT, digest, write_json

FAULTS = (
    ("lost-php-status-bit", 0x08, 1, "c->P|M6502_XF", "c->P"),
    ("wrong-pha-register", 0x48, 1, "c->A", "c->X"),
    ("lost-plp-flags", 0x28, 3, "c->P=(_GD()|M6502_BF)&~M6502_XF;", "c->P=c->P;"),
    ("lost-pla-flags", 0x68, 3, "_NZ(c->A);", ""),
    ("wrong-jmp-byte-order", 0x4C, 2, "c->PC=(_GD()<<8)|c->AD;", "c->PC=(c->AD<<8)|_GD();"),
    ("lost-indirect-jmp-wrap", 0x6C, 3, "_SA((c->AD&0xFF00)|((c->AD+1)&0x00FF));", "_SA(c->AD+1);"),
    ("wrong-jsr-return-high", 0x20, 2, "c->PC>>8", "(c->PC>>8)^1"),
    ("lost-rts-increment", 0x60, 4, "_SA(c->PC++);", "_SA(c->PC);"),
    ("lost-rti-status", 0x40, 3, "c->P=(_GD()|M6502_BF)&~M6502_XF;", "c->P=c->P;"),
)


def qualify():
    premises = [
        ROOT / "build/bbc" / name / "result.json"
        for name in (
            "cpu-c-control-induction-investigation",
            "cpu-c-structural-adapter",
            "cpu-c-memory-interrupt-bridge",
        )
    ]
    history, source_bound, _ = [memory_composition.sealed(path) for path in premises]
    parent = Path("/tmp/fpgaleet-c-control-induction")
    positive, original_contract = parent / "mapped.json", parent / "contract.v"
    for path in (positive, original_contract):
        if history["artifacts"].get(str(path)) != digest(path):
            raise ValueError("Changed full official history")
    prefix = Path("/tmp/fpgaleet-c-documented-memory/prefix.v")
    source = ROOT / "build/bbc/reference-cycle/m6502.h"
    if source_bound["inputs"].get(str(source.relative_to(ROOT))) != digest(source):
        raise ValueError("Changed actual C header")
    architecture = ROOT / "sim/formal/BbcCpuArchitectureContract.v"
    inputs = [
        Path(__file__),
        *[
            Path(m.__file__)
            for m in (
                documented_model,
                frontend,
                memory_interrupt_checks,
                prepared,
                prune,
                short_stream,
                structural,
                branch_induction,
                memory_composition,
            )
        ],
        Path(__file__).parents[1] / "video_cursor.py",
        *premises,
        positive,
        original_contract,
        prefix,
        source,
        architecture,
        ROOT / "tools/bbc/reference/official_stimulus.h",
        Path(shutil.which("gcc")),
        Path(shutil.which("yosys")),
        *sorted(Path(pycparser.__file__).parent.rglob("*.py")),
    ]
    before = hashes(inputs)
    work = Path("/tmp/fpgaleet-c-control-interrupt-checks")
    work.mkdir(exist_ok=True)
    allowed = documented_model.inventory()
    admission, artifacts = memory_interrupt_checks.admission(positive, work, allowed)
    print("Full official opcode domain: PASS", flush=True)
    contract = work / "contract.v"
    text = replace_once(
        original_contract.read_text(),
        "wire [255:0] cState,cHistory;",
        "(* keep *) wire cTrap;\nwire [255:0] cState,cHistory;",
    )
    text = replace_once(
        text, ".proofResponse(cResponse)", ".proofTrap(cTrap),.proofResponse(cResponse)"
    )
    text = replace_once(
        text,
        "endmodule",
        "(* keep *) wire definedObserved=reset || (!cTrap && sourceContext && observedEquivalent);\n(* keep *) wire faultObservable=reset || cTrap || observedEquivalent;\nendmodule",
    )
    contract.write_text(text)
    artifacts.append(contract)
    results = []
    for name, opcode, step, old, new in FAULTS:
        line = next(
            line
            for line in source.read_text().splitlines()
            if f"case (0x{opcode:02X}<<3)|{step}: " in line
        )
        mutated = replace_once(source.read_text(), line, replace_once(line, old, new))
        stimulus = f"-set firstOpcode {opcode} -set nextOpcode 234 -set irqPin 0 -set nmiPin 0"
        checks = []
        for label, header_text in (("positive", source.read_text()), ("fault", mutated)):
            case = work / name / label
            case.mkdir(parents=True, exist_ok=True)
            header, model = case / "reference.h", case / "source.v"
            header.write_text(header_text)
            expanded = subprocess.check_output(
                ["gcc", "-E", "-P", "-"], input=frontend.extract(header), text=True
            )
            # The negative stimulus already selects this opcode then NOP with
            # IRQ/NMI low. Keep BRK for the constructor's reset dispatch. Each
            # reduced correct model must first pass the same fresh query with
            # defined dispatch and no trap on every observed tick.
            model.write_text(
                prune.prune(
                    structural.generate(short_stream.adapter_ast(expanded, [0, opcode, 0xEA]))
                )[0]
            )
            graph, mapped, log, witness = [
                case / n for n in ("graph.json", "mapped.json", "proof.log", "witness.json")
            ]
            witness.unlink(missing_ok=True)
            command = f"read_verilog -formal -sv {model}; read_verilog -formal {prefix} {contract} {architecture}; prep -top BbcCpuInterruptStreamContract -flatten; check -assert; opt; memory_map; opt; chformal -lower; write_json {graph}; techmap; opt; abc -g AND; opt_clean; write_json {mapped}"
            cached = prepared.prepare(
                case,
                command,
                [model, prefix, contract, architecture],
                [graph, mapped],
                timeout=1800,
            )
            property_name = "definedObserved" if label == "positive" else "faultObservable"
            query = f"read_json {mapped}; sat -set-assumes -set-init-zero -seq 14 {stimulus} -prove {property_name} 1 -verify -timeout 300 -show {property_name} -show observedEquivalent -show cTrap -show firstOpcode -dump_json {witness}"
            with log.open("w") as output:
                run = subprocess.run(
                    ["yosys", "-Q", "-T", "-p", query],
                    stdout=output,
                    stderr=subprocess.STDOUT,
                    timeout=900,
                )
            for path in (graph, mapped):
                branch_induction.guard_domains(
                    json.loads(path.read_text()), "BbcCpuInterruptStreamContract"
                )
            contents = log.read_text()
            accepted = (
                run.returncode == 0 and "SAT proof finished - no model found: SUCCESS!" in contents
                if label == "positive"
                else run.returncode != 0
                and "ERROR: Called with -verify and proof did fail!" in contents
                and witness.is_file()
                and any(
                    signal["name"] == property_name and "0" in signal.get("wave", "")
                    for signal in json.loads(witness.read_text())["signal"]
                )
            )
            checks.append({"name": label, "accepted": accepted, "preparation_cache_reused": cached})
            artifacts += [
                header,
                model,
                graph,
                mapped,
                log,
                case / "prepared.json",
                case / "prepare.log",
                *([witness] if witness.exists() else []),
            ]
            print(name + "/" + label + (": PASS" if accepted else ": FAIL"), flush=True)
            if not accepted:
                break
        results.append(
            {
                "name": name,
                "passed": len(checks) == 2 and all(check["accepted"] for check in checks),
                "checks": checks,
                "stimulus": stimulus,
            }
        )
        if not results[-1]["passed"]:
            break
    stable = before == hashes(inputs)
    passed = stable and len(results) == len(FAULTS) and all(r["passed"] for r in results)
    write_json(
        ROOT / "build/bbc/cpu-c-full-official-interrupt-bridge/result.json",
        {
            "passed": passed,
            "inputs_unchanged": stable,
            "inputs": before,
            "artifacts": hashes(artifacts),
            "opcodes": allowed,
            "opcode_admission": admission,
            "faults": results,
            "scope": "Initialized all-151 official mixed source instruction histories with free IRQ/NMI, preservation-proved decoder and proved execution context. Universal opcode admission. Nine actual C control mutations need initialized observable counterexamples with trap-free failing ticks. Each small negative model has a matching correct-source, defined-execution positive check; only these bounded controls specialize the stimulus.",
            "limits": "Abstract quiet fetch entry. Actual OS reachability, enable/warm/reset histories and device/video/global scheduling composition remain open.",
            "universal_game_equivalence": False,
        },
    )
    if not passed:
        raise ValueError("Full official source interrupt controls incomplete")


if __name__ == "__main__":
    qualify()
