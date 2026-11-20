"""Compare physical wait/NMI histories with the independently sampled C CPU."""

import argparse
import json
import shutil
import subprocess
from pathlib import Path

from tools.bbc.proofs import memory_composition
from tools.bbc.proofs.cpu_reference import (
    documented_model,
    mixed_rtl,
    nmi_sampling,
    prepared,
    source_history,
)
from tools.bbc.proofs.cpu_reference.warm_checks import accepted_result
from tools.bbc.proofs.video_cursor import hashes, replace_once
from tools.validation.common import ROOT, write_json

DIRECTORY = ROOT / "build/bbc/cpu-source-physical-mixed-rtl"


def qualify(*, warm=False, documented=False):
    parents = [mixed_rtl.DIRECTORY / "result.json", nmi_sampling.DIRECTORY / "result.json"]
    if documented:
        parents.append(source_history.DIRECTORY / "result.json")
    for path in parents:
        memory_composition.sealed(path)
    directory = DIRECTORY.with_name("cpu-source-physical-warm-rtl") if warm else DIRECTORY
    if documented:
        directory = directory.with_name(directory.name + "-documented")
    directory.mkdir(parents=True, exist_ok=True)
    core = ROOT / "build/bbc/ArletCpuEnabled.v"
    model, original = [mixed_rtl.DIRECTORY / name for name in ("source.v", "contract.v")]
    if documented:
        model = source_history.DIRECTORY / "source.v"
    wrapper, alu = ROOT / "rtl/bbc/BbcCpu.v", ROOT / "third_party/6502/ALU.v"
    sampler = Path(__file__).with_name("nmi_sample.v")
    files = [
        Path(__file__),
        Path(mixed_rtl.__file__),
        Path(nmi_sampling.__file__),
        Path(prepared.__file__),
        Path(memory_composition.__file__),
        Path(__file__).with_name("warm_checks.py"),
        Path(__file__).parents[1] / "video_cursor.py",
        ROOT / "tools/validation/common.py",
        sampler,
        core,
        model,
        original,
        wrapper,
        alu,
        *parents,
        Path(shutil.which("yosys")),
    ]
    if documented:
        files.extend(
            [Path(documented_model.__file__), ROOT / "tools/bbc/reference/official_stimulus.h"]
        )
    before = hashes(files)
    body = replace_once(original.read_text(), "module MixedDirect(", "module PhysicalMixed(")
    body = replace_once(
        body,
        "reg fresh = 1;",
        "reg fresh = 1;\nreg enable = 0;\nalways @(posedge clk) enable <= !enable;",
    )
    body = replace_once(body, "if (!fresh && phase < 11)", "if (!fresh && enable && phase < 11)")
    body = replace_once(
        body,
        "wire [255:0] following;",
        "wire [255:0] following, sampled;\nM6502NmiSample sampler(.state(state), .nmi(nmi), .sampled(sampled));",
    )
    body = replace_once(body, ".state_in(state)", ".state_in(sampled)")
    body = replace_once(
        body,
        "always @(posedge clk) if (!fresh) begin",
        "always @(posedge clk) if (!fresh && enable) begin",
    )
    body = replace_once(
        body,
        "held <= requested;\n    end",
        "held <= requested;\n    end else if (!fresh) state <= sampled;",
    )
    body = replace_once(body, ".enable(!fresh)", ".enable(!fresh && enable)")
    body = replace_once(body, "if (!fresh) definedHistory", "if (!fresh && enable) definedHistory")
    if warm:
        body = replace_once(
            body,
            "reg fresh = 1;",
            "(* anyseq *) reg warmInput;\nwire warm=phase==11 && warmInput;\nreg fresh=1;",
        )
        body = replace_once(
            body, "({63'd0, nmi} << 27);", "({63'd0, nmi} << 27) | ({63'd0,warm} << 30);"
        )
        body = replace_once(body, ".warm_reset(1'b0)", ".warm_reset(warm)")
        body = replace_once(
            body,
            "else if (!fresh) state <= sampled;",
            "else if (!fresh) begin state<=sampled; pins<=pins|({63'd0,warm}<<30); end",
        )
    if documented:
        body = replace_once(
            body, "(* anyseq *) reg [1:0] opcodeChoice;", "(* anyseq *) reg [7:0] opcodeChoice;"
        )
        body = replace_once(
            body,
            "wire [7:0] opcode = opcodeChoice == 0 ? 8'h58 :\n                        opcodeChoice == 1 ? 8'h78 : 8'hea;",
            documented_model.context_function(documented_model.inventory())
            + "always @* assume(documented(opcodeChoice));\nwire [7:0] opcode=opcodeChoice;",
        )
    checks = []
    variants = [
        ("positive", body, True),
        (
            "lost-wait-edge",
            body.replace(".nmi(nmi), .sampled(sampled)", ".nmi(enable && nmi), .sampled(sampled)"),
            False,
        ),
        ("lost-rtl-nmi-poll", body, False),
    ]
    if warm:
        variants.append(
            (
                "lost-disabled-source-reset",
                replace_once(body, "pins<=pins|({63'd0,warm}<<30);", "pins<=pins;"),
                False,
            )
        )
    for name, text, correct in variants:
        case = directory / name
        case.mkdir(exist_ok=True)
        contract, altered = case / "contract.v", case / "cpu.v"
        contract.write_text(text)
        altered.write_text(
            replace_once(
                core.read_text(), "bbc_nmi_polled <= bbc_nmi_second;", "bbc_nmi_polled <= 0;"
            )
            if name == "lost-rtl-nmi-poll"
            else core.read_text()
        )
        graph, log, witness = [
            case / filename for filename in ("graph.json", "proof.log", "witness.json")
        ]
        prepared.prepare(
            case,
            f"read_verilog -formal -sv {model} {wrapper} {altered} {alu} {sampler} {contract}; prep -top PhysicalMixed -flatten; check -assert; async2sync; opt; memory_map; opt; techmap; opt; abc -g AND; opt_clean; write_json {graph}",
            [model, wrapper, altered, alu, sampler, contract],
            [graph],
        )
        module = json.loads(graph.read_text())["modules"]["PhysicalMixed"]
        assumptions = [
            cell for cell in module["cells"].values() if cell["type"] in ("$assume", "$check")
        ]
        expected = 1 if documented else 0
        if len(assumptions) != expected:
            raise ValueError("Unexpected physical comparison assumptions")
        prop = "equivalent" if correct else "faultDefined"
        steps = 38 if documented else 48
        solver_budget = 1200 if documented and correct else 300
        assume = "-set-assumes " if documented else ""
        warm_signal = "-show warmInput " if warm else ""
        query = (
            f"read_json {graph}; sat {assume}-set-init-zero -seq {steps} "
            f"-prove {prop} 1 -verify -timeout {solver_budget} "
            f"-show {prop} -show phase -show enable -show nmiInput -show irqInput "
            f"{warm_signal}-dump_json {witness}"
        )
        witness.unlink(missing_ok=True)
        try:
            with log.open("w") as output:
                trial = subprocess.run(
                    ["yosys", "-Q", "-T", "-p", query],
                    stdout=output,
                    stderr=subprocess.STDOUT,
                    timeout=solver_budget * 3 + 120,
                )
            evidence = log.read_text()
            accepted = accepted_result(trial.returncode, evidence, witness, prop, correct)
            status = "pass" if accepted else "fail"
            if "proof did time out!" in evidence or "Interrupted SAT solver: TIMEOUT!" in evidence:
                status = "timeout"
        except subprocess.TimeoutExpired:
            accepted, status = False, "timeout"
        checks.append({"name": name, "accepted": accepted, "status": status})
        print(f"Physical mixed C/RTL {name}: {status.upper()}", flush=True)
        if not accepted:
            break
    for path in parents:
        memory_composition.sealed(path)
    stable = before == hashes(files)
    passed = stable and len(checks) == len(variants) and all(check["accepted"] for check in checks)
    write_json(
        directory / "result.json",
        {
            "passed": passed,
            "inputs_unchanged": stable,
            "inputs": before,
            "artifacts": hashes(
                [
                    p
                    for check in checks
                    for p in (directory / check["name"]).glob("*")
                    if p.is_file()
                ]
            ),
            "checks": checks,
            "scope": (
                "38 physical clocks and all 151 documented opcodes."
                if documented
                else "48 physical clocks and CLI/SEI/NOP."
            )
            + " Alternating accepted/disabled edges; native cold startup, arbitrary IRQ/NMI levels and response bytes. Each engine issues its own bus requests. Reference NMI capture runs on every physical edge without aging its pipeline."
            + (
                " Warm requests can recur on any physical edge after startup, including waits."
                if warm
                else " Warm requests are inactive."
            ),
            "limits": "Bounded diagnostic with a fixed alternating enable schedule. Unbounded instruction histories and parent callback ordering require separate checks.",
            "universal_game_equivalence": False,
        },
    )
    if not passed:
        raise ValueError("Physical mixed source/RTL comparison incomplete")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--warm", action="store_true")
    parser.add_argument(
        "--documented", action="store_true", help="Use the complete documented opcode domain"
    )
    args = parser.parse_args()
    qualify(warm=args.warm, documented=args.documented)
