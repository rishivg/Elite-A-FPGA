"""Require actual C decoder faults to fail the complete instruction bridge."""

import json
import shutil
import subprocess
from pathlib import Path

from tools.bbc import cpu_instruction_proof
from tools.bbc.proofs.cpu_reference import frontend, instructions
from tools.bbc.proofs.video_cursor import hashes, replace_once
from tools.validation.common import ROOT, digest, write_json


def qualify():
    premise = ROOT / "build/bbc/cpu-c-instruction-bridge/result.json"
    positive = json.loads(premise.read_text())
    if (
        not positive.get("passed")
        or not positive.get("inputs_unchanged")
        or len(positive.get("opcodes", [])) != 151
    ):
        raise ValueError("Complete current all-151 C instruction bridge required")
    for group in ("inputs", "artifacts"):
        if any(digest(ROOT / name) != checksum for name, checksum in positive[group].items()):
            raise ValueError("Changed all-151 C instruction premise")
    source = ROOT / "build/bbc/reference-cycle/m6502.h"
    scalar = ROOT / "sim/formal/BbcCpuInstructionContract.v"
    control = ROOT / "sim/formal/BbcCpuControlContract.v"
    memory = ROOT / "sim/formal/BbcCpuMemoryContract.v"
    inventory = ROOT / "tools/bbc/reference/official_stimulus.h"
    architecture = ROOT / "sim/formal/BbcCpuArchitectureContract.v"
    directory = ROOT / "build/bbc/cpu-c-instruction-bridge-faults"
    directory.mkdir(exist_ok=True)
    work = Path("/tmp/fpgaleet-c-instruction-bridge-faults")
    work.mkdir(exist_ok=True)
    zero = next(
        text
        for mode, _, text in cpu_instruction_proof.memory_groups(
            memory.read_text(), inventory.read_text()
        )
        if mode == 1
    )
    faults = [
        (
            "wrong-clear-carry",
            0x18,
            "BbcCpuInstructionContract",
            scalar.read_text(),
            "case (0x18<<3)|1: c->P&=~0x1;_FETCH();break;",
            "case (0x18<<3)|1: c->P|=0x1;_FETCH();break;",
        ),
        (
            "lost-rmw-old-byte-write",
            0x06,
            "BbcCpuMemoryContract",
            zero,
            "case (0x06<<3)|2: c->AD=_GD();_WR();break;",
            "case (0x06<<3)|2: c->AD=_GD();_RD();break;",
        ),
        (
            "lost-indirect-pointer-increment",
            0x6C,
            "BbcCpuControlContract",
            control.read_text(),
            "case (0x6C<<3)|3: _SA((c->AD&0xFF00)|((c->AD+1)&0x00FF));c->AD=_GD();break;",
            "case (0x6C<<3)|3: _SA(c->AD);c->AD=_GD();break;",
        ),
    ]
    files = [
        Path(__file__),
        Path(instructions.__file__),
        Path(frontend.__file__),
        Path(cpu_instruction_proof.__file__),
        source,
        scalar,
        control,
        memory,
        inventory,
        architecture,
        premise,
        Path(shutil.which("gcc")),
        Path(shutil.which("yosys")),
    ]
    before = hashes(files)
    unit = work / "prefix.v"
    unit.write_text(instructions.prefix())
    results = []
    for name, opcode, top, contract, old, new in faults:
        case = work / name
        case.mkdir(exist_ok=True)
        modified = case / "fault.h"
        modified.write_text(replace_once(source.read_text(), old, new))
        expanded = subprocess.check_output(
            ["gcc", "-E", "-P", "-"], input=frontend.extract(modified), text=True
        )
        rtl = case / "fault.v"
        rtl.write_text(instructions.specialized(expanded, [opcode]))
        expectation = case / "contract.v"
        expectation.write_text(contract)
        log = case / "proof.log"
        graph = case / "graph.json"
        witness = case / "counterexample.json"
        witness.unlink(missing_ok=True)
        command = (
            f"read_verilog -formal -sv {rtl}; read_verilog -formal {expectation} {unit} {architecture}; prep -top {top} -flatten; check -assert; opt; chformal -lower; "
            + f"write_json {graph}; sat -set-assumes -set-init-zero -seq 17 -set opcode {opcode} -prove equivalent 1 -verify -timeout 120 -show equivalent -show opcode -show step -dump_json {witness}"
        )
        with log.open("w") as out:
            run = subprocess.run(
                ["yosys", "-Q", "-T", "-p", command],
                stdout=out,
                stderr=subprocess.STDOUT,
                timeout=180,
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
        if not detected:
            raise ValueError("Actual C instruction fault not detected: " + name)
        cpu_instruction_proof.guard_assumptions(json.loads(graph.read_text()), top)
        results.append(
            {
                "name": name,
                "opcode": f"{opcode:02x}",
                "detected": detected,
                "artifacts": hashes([modified, rtl, expectation, log, graph, witness]),
            }
        )
        print("Actual C fault detected: " + name, flush=True)
    stable = before == hashes(files)
    report = {
        "passed": stable,
        "inputs": before,
        "inputs_unchanged": stable,
        "faults": results,
        "artifacts": hashes([unit]),
        "scope": "Three actual source C decoder mutations, regenerated independently and checked with unchanged complete instruction bus/architectural contracts and the exact reference unit adapter from the all-151 premise. Each fault requires an initialized equivalence counterexample; tool errors or timeouts are rejected. Opcode-only assumption cones are checked.",
        "limits": "Complements the local source-derived instruction-unit theorem. Source translation trust, actual C cold entry, arbitrary opcode/interrupt streams and whole-machine/game composition remain separate.",
        "universal_game_equivalence": False,
    }
    write_json(directory / "result.json", report)
    if not stable:
        raise ValueError("C decoder fault inputs changed")
    return report


if __name__ == "__main__":
    qualify()
