"""Compare continuing C/RTL instruction streams across repeated warm requests."""

import json
import shutil
import subprocess
from pathlib import Path

from tools.bbc.proofs import memory_composition
from tools.bbc.proofs.cpu_reference import mixed_rtl, prepared
from tools.bbc.proofs.cpu_reference.warm_checks import accepted_result
from tools.bbc.proofs.video_cursor import hashes, replace_once
from tools.validation.common import ROOT, write_json

DIRECTORY = ROOT / "build/bbc/cpu-source-warm-mixed-rtl"


def qualify():
    parent_path = mixed_rtl.DIRECTORY / "result.json"
    memory_composition.sealed(parent_path)
    DIRECTORY.mkdir(parents=True, exist_ok=True)
    model, original = [mixed_rtl.DIRECTORY / name for name in ("source.v", "contract.v")]
    core = ROOT / "build/bbc/ArletCpuEnabled.v"
    wrapper, alu = ROOT / "rtl/bbc/BbcCpu.v", ROOT / "third_party/6502/ALU.v"
    files = [
        Path(__file__),
        parent_path,
        model,
        original,
        core,
        wrapper,
        alu,
        Path(mixed_rtl.__file__),
        Path(prepared.__file__),
        Path(memory_composition.__file__),
        Path(__file__).with_name("warm_checks.py"),
        Path(__file__).parents[1] / "video_cursor.py",
        ROOT / "tools/validation/common.py",
        Path(shutil.which("yosys")),
    ]
    before = hashes(files)
    body = replace_once(original.read_text(), "module MixedDirect(", "module WarmMixed(")
    body = replace_once(
        body,
        "reg fresh = 1;",
        "(* anyseq *) reg warmInput;\nwire warm=phase==11 && warmInput;\nreg fresh = 1;",
    )
    body = replace_once(body, "wire irq = phase == 11 && irqInput;", "wire irq=0;")
    body = replace_once(body, "wire nmi = phase == 11 && nmiInput;", "wire nmi=0;")
    body = replace_once(
        body, "({63'd0, nmi} << 27);", "({63'd0, nmi} << 27) | ({63'd0,warm} << 30);"
    )
    body = replace_once(body, ".warm_reset(1'b0)", ".warm_reset(warm)")
    checks = []
    for name, text, correct in (
        ("positive", body, True),
        (
            "lost-rtl-warm-request",
            replace_once(body, ".warm_reset(warm)", ".warm_reset(1'b0)"),
            False,
        ),
        (
            "lost-source-pending-res",
            replace_once(body, "(pins & ~64'hcff0000)", "(pins & ~64'h4cff0000)"),
            False,
        ),
    ):
        case = DIRECTORY / name
        case.mkdir(exist_ok=True)
        contract, graph, log, witness = [
            case / filename
            for filename in ("contract.v", "graph.json", "proof.log", "witness.json")
        ]
        contract.write_text(text)
        prepared.prepare(
            case,
            f"read_verilog -formal -sv {model} {wrapper} {core} {alu} {contract}; prep -top WarmMixed -flatten; check -assert; async2sync; opt; memory_map; opt; techmap; opt; abc -g AND; opt_clean; write_json {graph}",
            [model, wrapper, core, alu, contract],
            [graph],
        )
        module = json.loads(graph.read_text())["modules"]["WarmMixed"]
        if any(cell["type"] in ("$assume", "$check") for cell in module["cells"].values()):
            raise ValueError("Warm mixed comparison acquired an assumption")
        prop = "equivalent" if correct else "faultDefined"
        witness.unlink(missing_ok=True)
        with log.open("w") as output:
            trial = subprocess.run(
                [
                    "yosys",
                    "-Q",
                    "-T",
                    "-p",
                    f"read_json {graph}; sat -set-init-zero -seq 48 -prove {prop} 1 -verify -timeout 300 -show {prop} -show warmInput -show phase -dump_json {witness}",
                ],
                stdout=output,
                stderr=subprocess.STDOUT,
                timeout=600,
            )
        accepted = accepted_result(trial.returncode, log.read_text(), witness, prop, correct)
        checks.append({"name": name, "accepted": accepted})
        print("Warm mixed C/RTL " + name + (": PASS" if accepted else ": FAIL"), flush=True)
        if not accepted:
            break
    memory_composition.sealed(parent_path)
    stable = before == hashes(files)
    passed = stable and len(checks) == 3 and all(check["accepted"] for check in checks)
    write_json(
        DIRECTORY / "result.json",
        {
            "passed": passed,
            "inputs_unchanged": stable,
            "inputs": before,
            "artifacts": hashes(
                [
                    p
                    for check in checks
                    for p in (DIRECTORY / check["name"]).glob("*")
                    if p.is_file()
                ]
            ),
            "checks": checks,
            "scope": "48 continuously enabled clocks, native cold startup then CLI/SEI/NOP, arbitrary response bytes and repeated warm requests at any subsequent edge. Both source and RTL issue their own requests; compare address/RW/SYNC/write bytes throughout reset entry, vector reads and resumed instructions. Quiet IRQ/NMI are constructed before optimization. Lost actual warm requests and lost source RES retention must fail on defined source histories.",
            "limits": "Bounded selected-instruction warm histories with quiet interrupts and continuous enables. Does not establish arbitrary IRQ/NMI/wait/warm combinations, unbounded full-instruction histories or whole-game equivalence.",
            "universal_game_equivalence": False,
        },
    )
    if not passed:
        raise ValueError("Warm mixed source/RTL comparison incomplete")


if __name__ == "__main__":
    qualify()
