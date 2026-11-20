"""Investigate initialized control successors using the executing source model."""

import json
import shutil
import subprocess
from pathlib import Path

from tools.bbc.proofs import branch_induction, memory_composition
from tools.bbc.proofs.cpu_reference import documented_model, prepared
from tools.bbc.proofs.video_cursor import hashes, replace_once
from tools.validation.common import ROOT, digest, write_json


def qualify():
    premise = ROOT / "build/bbc/cpu-c-documented-memory-investigation/result.json"
    parent = memory_composition.sealed(premise)
    positive = Path("/tmp/fpgaleet-c-documented-memory")
    prefix, old_contract = positive / "prefix.v", positive / "contract.v"
    model = Path("/tmp/fpgaleet-c-documented-model/source.v")
    for path in (prefix, old_contract):
        if parent["artifacts"].get(str(path)) != digest(path):
            raise ValueError("Changed source history input")
    if parent["inputs"].get(str(model)) != digest(model):
        raise ValueError("Changed preservation-proved source decoder")
    architecture = ROOT / "sim/formal/BbcCpuArchitectureContract.v"
    work = Path("/tmp/fpgaleet-c-control-histories")
    work.mkdir(exist_ok=True)
    inputs = [
        Path(__file__),
        *[
            Path(m.__file__)
            for m in (branch_induction, memory_composition, documented_model, prepared)
        ],
        Path(__file__).parents[1] / "video_cursor.py",
        premise,
        prefix,
        old_contract,
        model,
        architecture,
        Path(shutil.which("yosys")),
    ]
    before = hashes(inputs)
    contract = work / "contract.v"
    text = old_contract.read_text()
    assumption = next(line for line in text.splitlines() if line.startswith("always @* assume("))
    text = replace_once(
        text, assumption, "always @* assume(documented(firstOpcode)&&documented(nextOpcode));"
    )
    # The existing memory-only induction relation is not asserted for this
    # diagnostic. Every bus/architecture observation and execution-context
    # check remains. No source or model transition is changed.
    text = replace_once(
        text,
        "assign equivalent=observedEquivalent && (reset || stateRelation);",
        "assign equivalent=observedEquivalent && (reset || sourceContext);",
    )
    contract.write_text(text)
    graph, mapped = work / "graph.json", work / "mapped.json"
    command = f"read_verilog -formal -sv {model}; read_verilog -formal {prefix} {contract} {architecture}; prep -top BbcCpuInterruptStreamContract -flatten; check -assert; opt; memory_map; opt; chformal -lower; write_json {graph}; techmap; opt; abc -g AND; opt_clean; write_json {mapped}"
    cached = prepared.prepare(
        work, command, [model, prefix, contract, architecture], [graph, mapped], timeout=1800
    )
    for path in (graph, mapped):
        branch_induction.guard_domains(
            json.loads(path.read_text()), "BbcCpuInterruptStreamContract"
        )
    checks = []
    artifacts = [contract, graph, mapped, work / "prepare.log", work / "prepared.json"]
    for opcode in (0x08, 0x48, 0x28, 0x68, 0x4C, 0x6C, 0x20, 0x60, 0x40):
        log, witness = work / f"{opcode:02x}.log", work / f"{opcode:02x}-witness.json"
        witness.unlink(missing_ok=True)
        query = f"read_json {mapped}; sat -set-assumes -set-init-zero -seq 18 -set firstOpcode {opcode} -set nextOpcode 234 -prove equivalent 1 -verify -timeout 300 -show equivalent -show observedEquivalent -show sourceContext -show stage -show actualOpcode -show cState -show cHistory -dump_json {witness}"
        with log.open("w") as output:
            run = subprocess.run(
                ["yosys", "-Q", "-T", "-p", query],
                stdout=output,
                stderr=subprocess.STDOUT,
                timeout=900,
            )
        contents = log.read_text()
        passed = run.returncode == 0 and "SAT proof finished - no model found: SUCCESS!" in contents
        checks.append(
            {
                "opcode": opcode,
                "passed": passed,
                "query": query,
                "counterexample": witness.is_file() and "proof did fail!" in contents,
            }
        )
        artifacts += [log, *([witness] if witness.exists() else [])]
        print(f"Control {opcode:02x}: {'PASS' if passed else 'FAIL'}", flush=True)
        if not passed:
            break
    stable = before == hashes(inputs)
    passed = stable and len(checks) == 9 and all(c["passed"] for c in checks)
    write_json(
        ROOT / "build/bbc/cpu-c-control-histories-investigation/result.json",
        {
            "passed": passed,
            "inputs_unchanged": stable,
            "inputs": before,
            "artifacts": hashes(artifacts),
            "checks": checks,
            "preparation_cache_reused": cached,
            "scope": "Initialized 18-tick diagnostic for each of nine remaining official control instructions followed by NOP, with free IRQ/NMI and response bytes. Actual source bus/architecture observations and dispatch context retained. No history induction claim.",
            "limits": "Finite specialized successor checks; mixed successors, unbounded control induction, admission/fault controls and global composition remain open.",
            "universal_game_equivalence": False,
        },
    )
    if not passed:
        raise ValueError("Source control history investigation incomplete")


if __name__ == "__main__":
    qualify()
