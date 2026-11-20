"""Qualify generalized fetch entry with small, preserved source histories."""

import json
import shutil
import subprocess
from pathlib import Path

from tools.bbc.proofs import branch_induction, memory_composition
from tools.bbc.proofs.cpu_reference import fetch_seed, prepared, reset_sequence
from tools.bbc.proofs.cpu_reference.warm_checks import accepted_result
from tools.bbc.proofs.video_cursor import hashes, replace_once
from tools.validation.common import ROOT, digest, write_json


def qualify():
    paths = [
        ROOT / "build/bbc" / name / "result.json"
        for name in ("cpu-c-reset-sequence", "cpu-c-control-induction-investigation")
    ]
    reset, history = [memory_composition.sealed(p) for p in paths]
    model = Path("/tmp/fpgaleet-c-reset-sequence/source.v")
    full = Path("/tmp/fpgaleet-c-reset-sequence/full.v")
    original_contract = Path("/tmp/fpgaleet-c-control-induction/contract.v")
    original_prefix = Path("/tmp/fpgaleet-c-documented-memory/prefix.v")
    architecture = ROOT / "sim/formal/BbcCpuArchitectureContract.v"
    for path, report, group in (
        (model, reset, "artifacts"),
        (full, reset, "artifacts"),
        (original_contract, history, "artifacts"),
        (original_prefix, history, "inputs"),
    ):
        if report[group].get(str(path)) != digest(path):
            raise ValueError("Changed source fetch-entry premise")
    inputs = [
        Path(__file__),
        *[
            Path(m.__file__)
            for m in (fetch_seed, prepared, reset_sequence, branch_induction, memory_composition)
        ],
        Path(__file__).with_name("warm_checks.py"),
        Path(__file__).parents[1] / "video_cursor.py",
        *paths,
        model,
        full,
        original_contract,
        original_prefix,
        architecture,
        Path(shutil.which("yosys")),
    ]
    before = hashes(inputs)
    work = Path("/tmp/fpgaleet-c-fetch-seed-checks")
    work.mkdir(exist_ok=True)
    small, preservation = work / "reduced.v", work / "preservation.v"
    small.write_text(
        replace_once(model.read_text(), "module M6502ReferenceNext(", "module Reduced(")
    )
    preservation.write_text(
        replace_once(
            reset_sequence.PRESERVATION,
            "wire context=(pins[25] && pins[30] && !pins[28]) || (!pins[25] && state[255:243]==0);",
            "wire context=pins[25] ? (pins[30] || pins[23:16]==8'h00 || pins[23:16]==8'hea) : "
            "(state[255:251]==0 && (state[250:243]==8'h00 || state[250:243]==8'hea));",
        )
    )
    graph, log, witness = [
        work / n for n in ("preservation.json", "preservation.log", "preservation-witness.json")
    ]
    preparation = work / "preservation"
    preparation.mkdir(exist_ok=True)
    command = f"read_verilog -formal -sv {full} {small} {preservation}; prep -top ResetDecoderPreservation -flatten; check -assert; opt; write_json {graph}"
    prepared.prepare(preparation, command, [full, small, preservation], [graph], timeout=1200)
    preserved = reset_sequence.run_query(
        graph, "ResetDecoderPreservation", log, witness, "equivalent"
    )
    artifacts = [
        small,
        preservation,
        graph,
        log,
        preparation / "prepared.json",
        preparation / "prepare.log",
    ]
    if witness.exists():
        artifacts.append(witness)
    checks = [{"name": "decoder-preservation", "accepted": preserved}]
    print("Fetch seed NOP/BRK preservation: " + ("PASS" if preserved else "FAIL"), flush=True)
    if preserved:
        for name, old, new in (
            ("positive", None, None),
            ("lost-retained-nmi", "{22'd0,seedNmi,9'd0}", "32'd0"),
            ("wrong-previous-nmi", "({63'b0,seedPrevious}<<27)", "({63'b0,!seedPrevious}<<27)"),
            ("early-retained-nmi", "{22'd0,seedNmi,9'd0}", "{21'd0,seedNmi,10'd0}"),
        ):
            case = work / name
            case.mkdir(exist_ok=True)
            prefix, contract = case / "prefix.v", case / "contract.v"
            text = fetch_seed.prefix(original_prefix.read_text())
            prefix.write_text(replace_once(text, old, new) if old else text)
            text = fetch_seed.contract(original_contract.read_text())
            text = replace_once(
                text,
                "wire [255:0] cState,cHistory;",
                "(* keep *) wire cTrap;\nwire [255:0] cState,cHistory;",
            )
            text = replace_once(
                text, ".proofResponse(cResponse)", ".proofTrap(cTrap),.proofResponse(cResponse)"
            )
            text = replace_once(
                text,
                "endmodule",
                "reg definedHistory=1;\nalways @(posedge clk) if(!reset)definedHistory<=definedHistory && !cTrap;\n(* keep *) wire definedObserved=reset || (!cTrap && sourceContext && observedEquivalent);\n(* keep *) wire faultObserved=reset || !definedHistory || cTrap || observedEquivalent;\nendmodule",
            )
            # These controls concern observable behavior, not the all-opcode
            # induction relation. Let that unused decoder cone disappear.
            text = replace_once(text, "(* keep *) wire stateRelation=", "wire stateRelation=")
            text = replace_once(
                text,
                "assign equivalent=observedEquivalent && (reset || stateRelation);",
                "assign equivalent=definedObserved;",
            )
            contract.write_text(text)
            graph, mapped, log, witness = [
                case / n for n in ("graph.json", "mapped.json", "proof.log", "witness.json")
            ]
            command = f"read_verilog -formal -sv {model}; read_verilog -formal {prefix} {contract} {architecture}; prep -top BbcCpuInterruptStreamContract -flatten; check -assert; opt; memory_map; opt; chformal -lower; write_json {graph}; techmap; opt; abc -g AND; opt_clean; write_json {mapped}"
            cached = prepared.prepare(
                case,
                command,
                [model, prefix, contract, architecture],
                [graph, mapped],
                timeout=1200,
            )
            for p in (graph, mapped):
                branch_induction.guard_domains(
                    json.loads(p.read_text()), "BbcCpuInterruptStreamContract"
                )
            # Only the separate bounded control query selects NOP successors.
            property_name = "definedObserved" if old is None else "faultObserved"
            witness.unlink(missing_ok=True)
            query = f"read_json {mapped}; sat -set-assumes -set-init-zero -seq 14 -set firstOpcode 234 -set nextOpcode 234 -prove {property_name} 1 -verify -timeout 120 -show {property_name} -dump_json {witness}"
            with log.open("w") as output:
                trial = subprocess.run(
                    ["yosys", "-Q", "-T", "-p", query],
                    stdout=output,
                    stderr=subprocess.STDOUT,
                    timeout=600,
                )
            accepted = accepted_result(
                trial.returncode, log.read_text(), witness, property_name, old is None
            )
            checks.append({"name": name, "accepted": accepted, "preparation_cache_reused": cached})
            artifacts += [
                prefix,
                contract,
                graph,
                mapped,
                log,
                case / "prepared.json",
                case / "prepare.log",
            ]
            if witness.exists():
                artifacts.append(witness)
            print("Fetch seed " + name + (": PASS" if accepted else ": FAIL"), flush=True)
            if not accepted:
                break
    for p in paths:
        memory_composition.sealed(p)
    stable = before == hashes(inputs)
    passed = stable and len(checks) == 5 and all(c["accepted"] for c in checks)
    write_json(
        ROOT / "build/bbc/cpu-c-fetch-seed-checks/result.json",
        {
            "passed": passed,
            "inputs_unchanged": stable,
            "inputs": before,
            "artifacts": hashes(artifacts),
            "checks": checks,
            "scope": "Preservation-checked NOP/BRK source decoder, 14 initialized ticks with NOP successors and unrestricted retained NMI, previous NMI level, older IRQ bits, live IRQ/NMI and response bytes. Correct source requires defined bus/architecture observations. Three abstract-entry mutations require observable counterexamples with the entire observed source history defined.",
            "limits": "Bounded entry fault controls only; no all-151 generalized-entry induction, universal seed admission, actual reset handoff, RTL or full-game composition claim.",
            "universal_game_equivalence": False,
        },
    )
    if not passed:
        raise ValueError("Generalized source entry controls incomplete")


if __name__ == "__main__":
    qualify()
