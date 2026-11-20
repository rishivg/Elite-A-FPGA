"""Check zero-page source IRQ/NMI admission and observable C fault detection."""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pycparser

from tools.bbc import cpu_instruction_proof
from tools.bbc.proofs import branch_induction, memory_composition
from tools.bbc.proofs.cpu_reference import (
    branch_interrupt_checks,
    frontend,
    prepared,
    prune,
    short_stream,
    structural,
)
from tools.bbc.proofs.video_cursor import hashes, replace_once
from tools.validation.common import ROOT, digest, write_json


def opcodes(contract):
    match = re.search(r"function zeroPage;(.+?)endfunction", contract, re.S)
    if not match:
        raise ValueError("Missing zero-page input domain")
    memory = {int(n, 16) for n in re.findall(r"op==8'h([0-9a-f]{2})", match[1])}
    result = sorted(set(cpu_instruction_proof.SCALAR_OPCODES) | memory)
    if len(memory) != 21 or len(result) != 63:
        raise ValueError("Changed zero-page opcode inventory")
    return result


def qualify():
    premise = ROOT / "build/bbc/cpu-c-zero-page-interrupts-investigation/result.json"
    source_premise = ROOT / "build/bbc/cpu-c-structural-adapter/result.json"
    history, source_bound = [memory_composition.sealed(p) for p in (premise, source_premise)]
    source = ROOT / "build/bbc/reference-cycle/m6502.h"
    if source_bound["inputs"].get(str(source.relative_to(ROOT))) != digest(source):
        raise ValueError("Changed maintained C source")
    positive = Path("/tmp/fpgaleet-c-zero-page-interrupts")
    contract, mapped = positive / "contract.v", positive / "mapped.json"
    for path in (contract, mapped):
        if history["artifacts"].get(str(path)) != digest(path):
            raise ValueError("Changed zero-page history model")
    prefix = Path("/tmp/fpgaleet-c-protocol-invariants/prefix.v")
    architecture = ROOT / "sim/formal/BbcCpuArchitectureContract.v"
    work = Path("/tmp/fpgaleet-c-zero-page-interrupt-checks")
    work.mkdir(exist_ok=True)
    directory = ROOT / "build/bbc/cpu-c-zero-page-interrupt-bridge"
    directory.mkdir(exist_ok=True)
    inputs = [
        Path(__file__),
        *[
            Path(m.__file__)
            for m in (
                branch_interrupt_checks,
                frontend,
                prepared,
                prune,
                short_stream,
                structural,
                branch_induction,
                memory_composition,
                cpu_instruction_proof,
            )
        ],
        Path(__file__).parents[1] / "video_cursor.py",
        premise,
        source_premise,
        source,
        contract,
        mapped,
        prefix,
        architecture,
        Path(shutil.which("yosys")),
        Path(shutil.which("gcc")),
        *sorted(Path(pycparser.__file__).parent.rglob("*.py")),
    ]
    before = hashes(inputs)
    allowed = opcodes(contract.read_text())
    admission, artifacts = branch_interrupt_checks.admission(mapped, work, allowed)
    print("All 3969 opcode pairs and 386 excluded slots: PASS", flush=True)
    faults = [
        (
            "wrong-store-address",
            0x85,
            "case (0x85<<3)|1: _SA(_GD());",
            "case (0x85<<3)|1: _SA((_GD()+1)&255);",
        ),
        (
            "wrong-store-register",
            0x85,
            "case (0x85<<3)|1: _SA(_GD());_SD(c->A);",
            "case (0x85<<3)|1: _SA(_GD());_SD(c->X);",
        ),
        (
            "lost-rmw-old-write",
            0x06,
            "case (0x06<<3)|2: c->AD=_GD();_WR();",
            "case (0x06<<3)|2: c->AD=_GD();",
        ),
        (
            "wrong-rmw-shift",
            0x06,
            "case (0x06<<3)|3: _SD(_m6502_asl(c,c->AD));",
            "case (0x06<<3)|3: _SD(_m6502_lsr(c,c->AD));",
        ),
        (
            "lost-rmw-new-write",
            0xE6,
            "case (0xE6<<3)|3: c->AD++;_NZ(c->AD);_SD(c->AD);_WR();",
            "case (0xE6<<3)|3: c->AD++;_NZ(c->AD);_SD(c->AD);",
        ),
        (
            "wrong-zero-page-read",
            0x05,
            "case (0x05<<3)|2: c->A|=_GD();",
            "case (0x05<<3)|2: c->A&=_GD();",
        ),
        (
            "lost-bit-flags",
            0x24,
            "case (0x24<<3)|2: _m6502_bit(c,_GD());",
            "case (0x24<<3)|2: c->P=c->P;",
        ),
        (
            "lost-nmi-hijack",
            0,
            "case (0x00<<3)|3: if(c->nmi_pip&&(0==(c->brk_flags&M6502_BRK_RESET)))",
            "case (0x00<<3)|3: if(0 && c->nmi_pip&&(0==(c->brk_flags&M6502_BRK_RESET)))",
        ),
    ]
    results = []
    for name, opcode, old, new in faults:
        case = work / name
        case.mkdir(exist_ok=True)
        header, model = case / "reference.h", case / "source.v"
        header.write_text(replace_once(source.read_text(), old, new))
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
        command = (
            f"read_verilog -formal -sv {model}; read_verilog -formal {prefix} {contract} {architecture}; "
            f"prep -top BbcCpuInterruptStreamContract -flatten; check -assert; opt; chformal -lower; write_json {graph}; "
            f"techmap; opt; abc -g AND; opt_clean; write_json {optimized}"
        )
        cached = prepared.prepare(
            case, command, [model, prefix, contract, architecture], [graph, optimized], timeout=1800
        )
        stimulus = f"-set firstOpcode {opcode} -set nextOpcode 234 "
        if name != "lost-nmi-hijack":
            stimulus += "-set irqPin 0 -set nmiPin 0 "
        query = (
            f"read_json {optimized}; sat -set-assumes -set-init-zero -seq 14 -prove observedEquivalent 1 -verify "
            f"{stimulus}-timeout 300 -show observedEquivalent -show firstOpcode -show nextOpcode -show irqPin -show nmiPin -dump_json {witness}"
        )
        with log.open("w") as output:
            trial = subprocess.run(
                ["yosys", "-Q", "-T", "-p", query],
                stdout=output,
                stderr=subprocess.STDOUT,
                timeout=900,
            )
        for path in (graph, optimized):
            branch_induction.guard_domains(
                json.loads(path.read_text()), "BbcCpuInterruptStreamContract"
            )
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
        results.append(
            {
                "name": name,
                "detected": detected,
                "preparation_cache_reused": cached,
                "negative_query_stimulus": stimulus.strip(),
            }
        )
        print(name + (": PASS" if detected else ": FAIL"), flush=True)
        if not detected:
            break
    stable = before == hashes(inputs)
    passed = stable and len(results) == 8 and all(r["detected"] for r in results)
    write_json(
        directory / "result.json",
        {
            "passed": passed,
            "inputs_unchanged": stable,
            "inputs": before,
            "artifacts": hashes(artifacts),
            "opcode_admission": admission,
            "opcodes": allowed,
            "faults": results,
            "scope": "Complete-decoder initialized source induction for 63 short/branch/BRK/zero-page opcodes, with arbitrary IRQ/NMI and response bytes. Fresh admission SAT for every opcode pair and excluded slot. Eight actual C header mutations must produce initialized observable bus/architecture counterexamples. Only negative queries use the recorded concrete opcode/interrupt stimuli; positive induction has the sole opcode-domain assumption.",
            "limits": "Abstract quiet fetch entry. Indexed/absolute/indirect memory and remaining control instructions, actual OS entry, enables/warm resets, reference scheduling/devices and whole-game equivalence remain separate.",
            "universal_game_equivalence": False,
        },
    )
    if not passed:
        raise ValueError("Zero-page source interrupt bridge incomplete")


if __name__ == "__main__":
    qualify()
