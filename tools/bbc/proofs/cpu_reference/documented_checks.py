"""Check reduced-decoder context admission and detect an omitted C opcode."""

import json
import shutil
import subprocess
from pathlib import Path

import pycparser

from tools.bbc.proofs import memory_composition
from tools.bbc.proofs.cpu_reference import (
    documented_model,
    frontend,
    prepared,
    prune,
    short_stream,
    structural,
)
from tools.bbc.proofs.video_cursor import hashes
from tools.validation.common import ROOT, digest, write_json


def qualify():
    premise = ROOT / "build/bbc/cpu-c-documented-model/result.json"
    positive = memory_composition.sealed(premise)
    origin = Path("/tmp/fpgaleet-c-documented-model")
    graph = origin / "mapped.json"
    if positive["artifacts"].get(str(graph)) != digest(graph):
        raise ValueError("Changed documented preservation graph")
    full = Path("/tmp/fpgaleet-c-structural-pruning/pruned.v")
    source = ROOT / "build/bbc/reference-cycle/m6502.h"
    work = Path("/tmp/fpgaleet-c-documented-checks")
    work.mkdir(exist_ok=True)
    destination = ROOT / "build/bbc/cpu-c-documented-model-checks/result.json"
    destination.parent.mkdir(exist_ok=True)
    files = [
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
                memory_composition,
            )
        ],
        Path(__file__).parents[1] / "video_cursor.py",
        premise,
        graph,
        full,
        source,
        Path(shutil.which("gcc")),
        Path(shutil.which("yosys")),
        *sorted(Path(pycparser.__file__).parent.rglob("*.py")),
    ]
    before = hashes(files)
    values = positive["opcodes"]
    excluded = sorted(set(range(256)) - set(values))
    # Yosys SAT set expressions in this version do not accept part selects.
    # Give the existing IR bits a read-only alias; other state bits stay free.
    admission_graph = work / "admission.json"
    content = json.loads(graph.read_text())
    top = content["modules"]["DocumentedModelContract"]
    top["netnames"]["dispatch_ir"] = {
        "hide_name": 0,
        "bits": top["netnames"]["state_in"]["bits"][240:256],
        "attributes": {"keep": "1"},
    }
    write_json(admission_graph, content)
    commands = [
        f"read_json {admission_graph}",
        "delete -output DocumentedModelContract/w:equivalent",
        "opt_clean -purge",
    ]
    for opcode in range(256):
        expected = int(opcode in values)
        # Verify both ordinary dispatch and SYNC replacement, admitting all
        # other state bits. SYNC ignores old IR even when it is undocumented.
        commands.append(
            f"sat -set pins_in 16777216 -set dispatch_ir {opcode * 8} -prove context {expected} -verify"
        )
        commands.append(
            f"sat -set pins_in {50331648 | (opcode << 16)} -prove context {expected} -verify"
        )
    commands += [
        "sat -set pins_in 285212672 -prove context 1 -verify",
        "sat -set pins_in 16777216 -set dispatch_ir 65535 -prove context 0 -verify",
    ]
    script, log = work / "admission.ys", work / "admission.log"
    script.write_text("\n".join(commands) + "\n")
    with log.open("w") as output:
        subprocess.run(
            ["yosys", "-Q", "-T", "-s", str(script)],
            stdout=output,
            stderr=subprocess.STDOUT,
            check=True,
            timeout=300,
        )
    if log.read_text().count("SAT proof finished - no model found: SUCCESS!") != 514:
        raise ValueError("Incomplete documented context admission")
    print("Context admission/exclusion and RDY hold: PASS", flush=True)
    expanded = subprocess.check_output(
        ["gcc", "-E", "-P", "-"], input=frontend.extract(source), text=True
    )
    model = work / "omitted-adc.v"
    model.write_text(
        prune.prune(
            structural.generate(
                short_stream.adapter_ast(expanded, [n for n in values if n != 0x65])
            )
        )[0]
    )
    contract = work / "contract.v"
    contract.write_text(documented_model.contract(values))
    mapped, word = work / "fault-mapped.json", work / "fault-graph.json"
    command = f"read_verilog -formal -sv {full}; rename M6502ReferenceNext Full; read_verilog -formal -sv {model}; rename M6502ReferenceNext Reduced; read_verilog -formal {contract}; prep -top DocumentedModelContract -flatten; check -assert; opt; memory_map; opt; write_json {word}; techmap; opt; abc -g AND; opt_clean; write_json {mapped}"
    cached = prepared.prepare(work, command, [full, model, contract], [word, mapped], timeout=1800)
    fault_log, witness = work / "fault.log", work / "witness.json"
    witness.unlink(missing_ok=True)
    state = ((0x65 * 8 + 2) << 240) | (1 << 200)
    query = f"read_json {mapped}; sat -set state_in 256'h{state:064x} -set pins_in 16842752 -prove equivalent 1 -verify -timeout 120 -show equivalent -show context -dump_json {witness}"
    with fault_log.open("w") as output:
        trial = subprocess.run(
            ["yosys", "-Q", "-T", "-p", query], stdout=output, stderr=subprocess.STDOUT, timeout=300
        )
    detected = (
        trial.returncode != 0
        and "ERROR: Called with -verify and proof did fail!" in fault_log.read_text()
        and witness.exists()
        and any(
            s["name"] == "equivalent" and "0" in s.get("wave", "")
            for s in json.loads(witness.read_text())["signal"]
        )
    )
    stable = before == hashes(files)
    passed = stable and detected
    write_json(
        destination,
        {
            "passed": passed,
            "inputs_unchanged": stable,
            "inputs": before,
            "artifacts": hashes([p for p in work.iterdir() if p.is_file()]),
            "allowed_context_checks": len(values) * 2,
            "excluded_context_checks": len(excluded) * 2,
            "hold_and_invalid_ir_checks": 2,
            "omitted_adc_detected": detected,
            "preparation_cache_reused": cached,
            "scope": "Fresh universal context-admission/exclusion checks for all 256 opcodes in ordinary and SYNC dispatch, plus arbitrary-state RDY read hold and invalid high IR. A C-derived decoder deliberately omitting official zero-page ADC must fail the original full-vs-reduced property at a recorded valid concrete context. No changed C source or positive verdict is inherited.",
            "limits": "Qualifies documented-decoder preservation only. Every history must separately prove its executing context is admitted; CPU/game reachability and full machine composition remain separate.",
            "universal_game_equivalence": False,
        },
    )
    print("Omitted official ADC detection: " + ("PASS" if detected else "FAIL"), flush=True)
    if not passed:
        raise ValueError("Documented-model controls incomplete")


if __name__ == "__main__":
    qualify()
