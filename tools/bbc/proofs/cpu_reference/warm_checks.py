"""Qualify the bounded source restart diagnostic with observable faults."""

import json
import shutil
import subprocess
from pathlib import Path

import pycparser

from tools.bbc.proofs import branch_induction, memory_composition
from tools.bbc.proofs.cpu_reference import (
    frontend,
    prepared,
    prune,
    short_stream,
    specialized,
    structural,
)
from tools.bbc.proofs.video_cursor import hashes, replace_once
from tools.validation.common import ROOT, digest, write_json

FAULTS = (
    ("wrong-reset-vector", "c->AD=0xFFFC;", "c->AD=0xFFFE;"),
    (
        "reset-stack-write",
        "case (0x00<<3)|2: _SAD(0x0100|c->S--,c->PC);if(0==(c->brk_flags&M6502_BRK_RESET)){_WR();}break;",
        "case (0x00<<3)|2: _SAD(0x0100|c->S--,c->PC);_WR();break;",
    ),
    ("ignored-reset-request", "if (0 != (pins & M6502_RES)) {", "if (0) {"),
    ("lost-pending-reset", None, None),
)


def observed_contract(text):
    text = replace_once(
        text,
        "wire [255:0] cState,cHistory;",
        "(* keep *) wire cTrap;\nwire [255:0] cState,cHistory;",
    )
    text = replace_once(
        text, ".proofResponse(cResponse)", ".proofTrap(cTrap),.proofResponse(cResponse)"
    )
    return replace_once(
        text,
        "endmodule",
        "(* keep *) wire definedObserved=reset || (!cTrap && sourceContext && observedEquivalent);\n"
        "(* keep *) wire faultObservable=reset || cTrap || observedEquivalent;\nendmodule",
    )


def accepted_result(returncode, log, witness, property_name, positive):
    if positive:
        return returncode == 0 and "SAT proof finished - no model found: SUCCESS!" in log
    return (
        returncode != 0
        and "ERROR: Called with -verify and proof did fail!" in log
        and witness.is_file()
        and any(
            signal["name"] == property_name and "0" in signal.get("wave", "")
            for signal in json.loads(witness.read_text())["signal"]
        )
    )


def qualify():
    premise = ROOT / "build/bbc/cpu-c-warm-histories-investigation/result.json"
    parent = memory_composition.sealed(premise)
    original = Path("/tmp/fpgaleet-c-warm-histories")
    original_prefix, original_contract = original / "prefix.v", original / "contract.v"
    for path in (original_prefix, original_contract):
        if parent["artifacts"].get(str(path)) != digest(path):
            raise ValueError("Changed source restart diagnostic: " + str(path))
    source = ROOT / "build/bbc/reference-cycle/m6502.h"
    source_premise = ROOT / "build/bbc/cpu-c-structural-adapter/result.json"
    source_bound = memory_composition.sealed(source_premise)
    if source_bound["inputs"].get(str(source.relative_to(ROOT))) != digest(source):
        raise ValueError("Changed actual C header")
    architecture = ROOT / "sim/formal/BbcCpuArchitectureContract.v"
    inputs = [
        Path(__file__),
        *[
            Path(module.__file__)
            for module in (
                frontend,
                prepared,
                prune,
                short_stream,
                specialized,
                structural,
                branch_induction,
                memory_composition,
            )
        ],
        Path(__file__).parents[1] / "video_cursor.py",
        premise,
        source_premise,
        original_prefix,
        original_contract,
        source,
        architecture,
        Path(shutil.which("gcc")),
        Path(shutil.which("yosys")),
        *sorted(Path(pycparser.__file__).parent.rglob("*.py")),
    ]
    before = hashes(inputs)
    work = Path("/tmp/fpgaleet-c-warm-checks")
    work.mkdir(exist_ok=True)
    contract = work / "contract.v"
    contract.write_text(observed_contract(original_contract.read_text()))
    artifacts = [contract]
    results = []
    cases = [("positive", None, None), *FAULTS]
    for name, old, new in cases:
        case = work / name
        case.mkdir(exist_ok=True)
        header, model, prefix = [case / n for n in ("reference.h", "source.v", "prefix.v")]
        header.write_text(replace_once(source.read_text(), old, new) if old else source.read_text())
        prefix_text = original_prefix.read_text()
        if name == "lost-pending-reset":
            # Both the saved pins and explicit latch retain RES in the real
            # wrapper. Drop both retention paths, leaving only its pulse.
            prefix_text = replace_once(
                prefix_text, "referencePins & ~64'hcff0000", "referencePins & ~64'h4cff0000"
            )
            prefix_text = replace_once(prefix_text, "requestPending||warmRequest", "warmRequest")
        prefix.write_text(prefix_text)
        expanded = subprocess.check_output(
            ["gcc", "-E", "-P", "-"], input=frontend.extract(header), text=True
        )
        # All queries select NOP successors. BRK includes hardware interrupt
        # and reset dispatch. A fresh correct-source query first checks that
        # this smaller decoder never traps and preserves all observations.
        model.write_text(
            prune.prune(structural.generate(short_stream.adapter_ast(expanded, [0, 0xEA])))[0]
        )
        graph, mapped, log, witness = [
            case / n for n in ("graph.json", "mapped.json", "proof.log", "witness.json")
        ]
        witness.unlink(missing_ok=True)
        command = f"read_verilog -formal -sv {model}; read_verilog -formal {prefix} {contract} {architecture}; prep -top BbcCpuInterruptStreamContract -flatten; check -assert; opt; memory_map; opt; chformal -lower; write_json {graph}; techmap; opt; abc -g AND; opt_clean; write_json {mapped}"
        cached = prepared.prepare(
            case, command, [model, prefix, contract, architecture], [graph, mapped], timeout=900
        )
        for path in (graph, mapped):
            branch_induction.guard_domains(
                json.loads(path.read_text()), "BbcCpuInterruptStreamContract"
            )
        positive = name == "positive"
        property_name = "definedObserved" if positive else "faultObservable"
        # The correct-source case retains free IRQ/NMI. Negative controls
        # specialize them to isolate reset behavior; warmAt stays free.
        stimulus = "-set firstOpcode 234 -set nextOpcode 234"
        if not positive:
            stimulus += " -set irqPin 0 -set nmiPin 0"
        # These constants already restrict the query. Propagating them before
        # SAT removes unreachable combinational cones without narrowing its
        # stimulus or changing the initialized horizon. The unconstrained
        # graph above remains available for its domain guard and audit.
        specialization = case / "specialized"
        specialization.mkdir(exist_ok=True)
        fixed_graph, sat_graph = specialization / "fixed.json", specialization / "graph.json"
        bindings = {"firstOpcode": 234, "nextOpcode": 234}
        if not positive:
            bindings.update(irqPin=0, nmiPin=0)
        fixed_graph.write_text(
            json.dumps(
                specialized.substitute(
                    json.loads(mapped.read_text()), "BbcCpuInterruptStreamContract", bindings
                )
            )
        )
        specialization_cached = prepared.prepare(
            specialization,
            f"read_json {fixed_graph}; opt -full; check -assert; write_json {sat_graph}",
            [fixed_graph],
            [sat_graph],
            timeout=300,
        )
        specialized.check_constants(
            json.loads(sat_graph.read_text()), "BbcCpuInterruptStreamContract", bindings
        )
        query = f"read_json {sat_graph}; sat -set-assumes -set-init-zero -seq 24 {stimulus} -prove {property_name} 1 -verify -timeout 300 -show {property_name} -show observedEquivalent -show cTrap -show warmAt -show warmRequest -show resetEntry -show stage -dump_json {witness}"
        with log.open("w") as output:
            run = subprocess.run(
                ["yosys", "-Q", "-T", "-p", query],
                stdout=output,
                stderr=subprocess.STDOUT,
                timeout=900,
            )
        accepted = accepted_result(
            run.returncode, log.read_text(), witness, property_name, positive
        )
        results.append(
            {
                "name": name,
                "accepted": accepted,
                "preparation_cache_reused": cached,
                "specialization_cache_reused": specialization_cached,
                "stimulus": stimulus,
            }
        )
        artifacts += [
            header,
            model,
            prefix,
            graph,
            mapped,
            log,
            case / "prepared.json",
            case / "prepare.log",
            sat_graph,
            fixed_graph,
            specialization / "prepared.json",
            specialization / "prepare.log",
            *([witness] if witness.exists() else []),
        ]
        print("Source warm control " + name + (": PASS" if accepted else ": FAIL"), flush=True)
        if not accepted:
            break
    stable = before == hashes(inputs)
    passed = stable and len(results) == len(cases) and all(r["accepted"] for r in results)
    write_json(
        ROOT / "build/bbc/cpu-c-warm-checks/result.json",
        {
            "passed": passed,
            "inputs_unchanged": stable,
            "inputs": before,
            "artifacts": hashes(artifacts),
            "checks": results,
            "scope": "Initialized 24-tick NOP successor restart diagnostic. One freely timed request; correct reduced source proves defined dispatch and bus/architecture observations with free IRQ/NMI. Three actual-C reset mutations and one dropped pending-request wrapper fault require trap-free observable counterexamples under the same horizon, with IRQ/NMI low.",
            "limits": "Bounded source restart qualification; no all-opcode, unbounded warm/enable, OS-entry or whole-machine equivalence claim.",
            "universal_game_equivalence": False,
        },
    )
    if not passed:
        raise ValueError("Source warm controls incomplete")


if __name__ == "__main__":
    qualify()
