"""Investigate initialized mixed official instructions with a source restart."""

import argparse
import json
import shutil
import subprocess
from pathlib import Path

from tools.bbc.proofs import branch_induction, memory_composition
from tools.bbc.proofs.cpu_reference import documented_model, memory_interrupt_checks, prepared
from tools.bbc.proofs.video_cursor import hashes, replace_once
from tools.validation.common import ROOT, digest, write_json


def qualify(steps=24, timeout=900):
    # elapsed saturates at 31. At tick 32, warmAt==31 would assert a held
    # request rather than the single request investigated by this target.
    if not 5 <= steps <= 31 or not 1 <= timeout <= 3600:
        raise ValueError("Select 5–31 initialized ticks and a 1–3600 second SAT timeout")
    premise = ROOT / "build/bbc/cpu-c-warm-histories-investigation/result.json"
    parent = memory_composition.sealed(premise)
    directory = Path("/tmp/fpgaleet-c-warm-histories")
    original_prefix, contract = directory / "prefix.v", directory / "contract.v"
    for path in (original_prefix, contract):
        if parent["artifacts"].get(str(path)) != digest(path):
            raise ValueError("Changed initialized source restart input")
    model = Path("/tmp/fpgaleet-c-documented-model/source.v")
    if parent["inputs"].get(str(model)) != digest(model):
        raise ValueError("Changed preservation-proved source decoder")
    architecture = ROOT / "sim/formal/BbcCpuArchitectureContract.v"
    inputs = [
        Path(__file__),
        *[
            Path(module.__file__)
            for module in (
                branch_induction,
                memory_composition,
                documented_model,
                memory_interrupt_checks,
                prepared,
            )
        ],
        Path(__file__).parents[1] / "video_cursor.py",
        premise,
        original_prefix,
        contract,
        model,
        architecture,
        ROOT / "tools/bbc/reference/official_stimulus.h",
        Path(shutil.which("yosys")),
    ]
    before = hashes(inputs)
    work = Path("/tmp/fpgaleet-c-warm-mixed/reset-aware") / str(steps)
    work.mkdir(parents=True, exist_ok=True)
    preparation = work.parent / "prepared"
    preparation.mkdir(exist_ok=True)
    prefix = preparation / "prefix.v"
    prefix.write_text(reset_aware_prefix(original_prefix.read_text()))
    graph, mapped = preparation / "graph.json", preparation / "mapped.json"
    command = f"read_verilog -formal -sv {model}; read_verilog -formal {prefix} {contract} {architecture}; prep -top BbcCpuInterruptStreamContract -flatten; check -assert; opt; memory_map; opt; chformal -lower; write_json {graph}; techmap; opt; abc -g AND; opt_clean; write_json {mapped}"
    cached = prepared.prepare(
        preparation, command, [model, prefix, contract, architecture], [graph, mapped], timeout=1800
    )
    for path in (graph, mapped):
        branch_induction.guard_domains(
            json.loads(path.read_text()), "BbcCpuInterruptStreamContract"
        )
    admission, artifacts = memory_interrupt_checks.admission(
        mapped, work, documented_model.inventory()
    )
    print("Mixed source warm opcode domain: PASS", flush=True)
    log, witness = work / "proof.log", work / "witness.json"
    witness.unlink(missing_ok=True)
    query = f"read_json {mapped}; sat -set-assumes -set-init-zero -seq {steps} -prove equivalent 1 -verify -timeout {timeout} -show equivalent -show observedEquivalent -show sourceContext -show firstOpcode -show nextOpcode -show warmAt -show warmRequest -show stage -show resetEntry -show cState -dump_json {witness}"
    with log.open("w") as output:
        run = subprocess.run(
            ["yosys", "-Q", "-T", "-p", query],
            stdout=output,
            stderr=subprocess.STDOUT,
            timeout=timeout + 600,
        )
    memory_composition.sealed(premise)
    stable = before == hashes(inputs)
    passed = (
        stable
        and run.returncode == 0
        and "SAT proof finished - no model found: SUCCESS!" in log.read_text()
    )
    write_json(
        ROOT / f"build/bbc/cpu-c-warm-mixed-investigation/{steps}-reset-aware-result.json",
        {
            "passed": passed,
            "inputs_unchanged": stable,
            "inputs": before,
            "artifacts": hashes(
                [
                    prefix,
                    graph,
                    mapped,
                    preparation / "prepare.log",
                    preparation / "prepared.json",
                    *artifacts,
                    log,
                    *([witness] if witness.exists() else []),
                ]
            ),
            "preparation_cache_reused": cached,
            "opcode_admission": admission,
            "steps": steps,
            "timeout": timeout,
            "scope": "Initialized bounded actual-C restart history with mixed 151-official-opcode successors, arbitrary IRQ/NMI and response bytes, and one freely timed warm request. Unchanged preservation-proved source decoder and executing state/response drivers. Read-only startup flag check accounts for reset preempting the first SEI/CLI. Independent bus/architecture observations and source dispatch context checked by fresh SAT; no prior verdict reused.",
            "limits": "Bounded investigation. Warm request times beyond the horizon are admitted. No unbounded warm/enable, real OS entry or whole-machine equivalence claim.",
            "universal_game_equivalence": False,
        },
    )
    print("Mixed source warm history: " + ("PASS" if passed else "FAIL"), flush=True)
    if not passed:
        raise ValueError("Mixed source restart investigation incomplete")


def reset_aware_prefix(text):
    # The fixed age-28 startup check normally observes the first instruction's
    # early CLI/SEI flag update. A reset at that first SYNC skips the instruction,
    # so its expected I flag is the original prefix P instead. This observation
    # register does not drive the C engine, responses or architecture model.
    return replace_once(
        text,
        "wire [7:0] initialP = opcode == 8'h78 ? p | 8'h04 :",
        "reg firstReset=0;\n"
        "  always @(posedge clk) if(reset)firstReset<=0;else if(age==26)firstReset<=response[30];\n"
        "  wire [7:0] initialP = firstReset ? p : opcode == 8'h78 ? p | 8'h04 :",
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--steps", type=int, default=24)
    parser.add_argument("--timeout", type=int, default=900)
    args = parser.parse_args()
    qualify(args.steps, args.timeout)
