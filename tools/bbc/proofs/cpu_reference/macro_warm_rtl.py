"""Compare warm CPU restarts across arbitrary physical wait blocks."""

import argparse
import json
import shutil
import subprocess
from pathlib import Path

from tools.bbc.proofs import memory_composition, restart_macro
from tools.bbc.proofs.cpu_reference import (
    documented_model,
    macro_mixed_rtl,
    mixed_rtl,
    prepared,
    source_history,
)
from tools.bbc.proofs.cpu_reference.warm_checks import accepted_result
from tools.bbc.proofs.video_cursor import hashes, replace_once
from tools.validation.common import ROOT, write_json

BBC = ROOT / "build/bbc"
DIRECTORY = BBC / "cpu-source-macro-warm-rtl"


def qualify(*, documented=False):
    directory = DIRECTORY.with_name(DIRECTORY.name + "-documented") if documented else DIRECTORY
    steps = 24 if documented else 36
    mixed = macro_mixed_rtl.DIRECTORY / "result.json"
    waits = BBC / "cpu-restart-idle-summary/result.json"
    actual = BBC / "cpu-restart-macro-transition-proof/result.json"
    parents = [mixed, waits, actual]
    if documented:
        parents.append(source_history.DIRECTORY / "result.json")
    for path in parents:
        memory_composition.sealed(path)
    core, wrapper = BBC / "ArletCpuEnabled.v", ROOT / "rtl/bbc/BbcCpu.v"
    source = (source_history.DIRECTORY if documented else mixed_rtl.DIRECTORY) / "source.v"
    alu = ROOT / "third_party/6502/ALU.v"
    sampler = Path(__file__).with_name("nmi_sample.v")
    prefix = macro_mixed_rtl.DIRECTORY / "positive/contract.v"
    files = [
        Path(__file__),
        Path(__file__).with_name("warm_checks.py"),
        Path(restart_macro.__file__),
        Path(prepared.__file__),
        Path(memory_composition.__file__),
        Path(shutil.which("yosys")),
        *parents,
        core,
        wrapper,
        source,
        alu,
        sampler,
        prefix,
    ]
    if documented:
        files.extend(
            [Path(documented_model.__file__), ROOT / "tools/bbc/reference/official_stimulus.h"]
        )
    before = hashes(files)
    macro_core, macro_wrapper = restart_macro.transform(core.read_text(), wrapper.read_text())
    if (
        actual.with_name("macro-core.v").read_text(),
        actual.with_name("macro-wrapper.v").read_text(),
    ) != (macro_core, macro_wrapper):
        raise ValueError("Different warm macro in the proved physical transition")
    body = replace_once(prefix.read_text(), "module MacroMixed(", "module MacroWarm(")
    body = replace_once(
        body,
        "reg fresh = 1;",
        """(* anyseq *) reg waitWarm, acceptedWarm;
wire pendingWarm=phase==11 && waitWarm;
wire freshWarm=phase==11 && acceptedWarm;
reg fresh=1;""",
    )
    body = replace_once(
        body,
        "({63'd0, nmi} << 27);",
        "({63'd0, nmi} << 27) | ({63'd0,pendingWarm||freshWarm}<<30);",
    )
    body = replace_once(body, ".warm_reset(1'b0)", ".warm_reset(freshWarm),.offWarm(pendingWarm)")
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
    variants = {
        "positive": body,
        "lost-source-wait-reset": replace_once(body, "pendingWarm||freshWarm", "freshWarm"),
        "lost-rtl-wait-reset": replace_once(body, ".offWarm(pendingWarm)", ".offWarm(1'b0)"),
    }
    directory.mkdir(parents=True, exist_ok=True)
    dut = directory / "macro.v"
    dut.write_text(macro_core + "\n" + macro_wrapper)
    checks, artifacts = [], [dut]
    for name, text in variants.items():
        case = directory / name
        case.mkdir(exist_ok=True)
        prop, graph, log, witness = (
            case / filename
            for filename in ("contract.v", "graph.json", "proof.log", "witness.json")
        )
        prop.write_text(text)
        prepared.prepare(
            case,
            f"read_verilog -formal -sv {source} {dut} {alu} {sampler} {prop}; prep -top MacroWarm -flatten; "
            f"check -assert; async2sync; opt; memory_map; opt; techmap; opt; abc -g AND; opt_clean; write_json {graph}",
            [source, dut, alu, sampler, prop],
            [graph],
        )
        module = json.loads(graph.read_text())["modules"]["MacroWarm"]
        assumptions = sum(
            cell["type"] in ("$assume", "$check") for cell in module["cells"].values()
        )
        if assumptions != int(documented):
            raise ValueError("Unexpected warm macro comparison assumptions")
        positive = name == "positive"
        signal = "equivalent" if positive else "faultDefined"
        incremental = documented and positive
        mode = f"-tempinduct-baseonly -maxsteps {steps}" if incremental else f"-seq {steps}"
        assume = "-set-assumes " if documented else ""
        solver_budget = 1800 if incremental else 300
        witness.unlink(missing_ok=True)
        query = (
            f"read_json {graph}; sat {assume}-set-init-zero {mode} -prove {signal} 1 "
            f"-verify -timeout {solver_budget} -show {signal} -show waitWarm "
            f"-show acceptedWarm -dump_json {witness}"
        )
        try:
            with log.open("w") as output:
                trial = subprocess.run(
                    ["yosys", "-Q", "-T", "-p", query],
                    stdout=output,
                    stderr=subprocess.STDOUT,
                    timeout=14400 if incremental else 1000,
                )
            evidence = log.read_text()
            if incremental:
                passed = (
                    trial.returncode == 0
                    and f"proved base case for {steps} steps: SUCCESS!" in evidence
                    and not witness.exists()
                )
            else:
                passed = accepted_result(trial.returncode, evidence, witness, signal, positive)
            status = "pass" if passed else "fail"
            if "proof did time out!" in evidence or "Interrupted SAT solver: TIMEOUT!" in evidence:
                status = "timeout"
        except subprocess.TimeoutExpired:
            passed, status = False, "timeout"
        checks.append({"name": name, "accepted": passed, "status": status})
        artifacts.extend(p for p in (prop, graph, log, witness) if p.is_file())
        print(f"Warm wait-block comparison {name}: {status.upper()}", flush=True)
        if not passed:
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
            "artifacts": hashes(artifacts),
            "checks": checks,
            "scope": f"{steps} accepted clocks from cold startup. "
            + ("All 151 documented opcodes. " if documented else "CLI, SEI and NOP. ")
            + "Arbitrary IRQ/NMI and response bytes, with repeated warm-reset requests including those seen during unrestricted physical wait blocks. Wait blocks are represented by NMI rise/level and pending-resume summaries. The actual RTL macro matches the separately checked 168-bit physical transition. The independent C state retains the scheduler's RES and NMI latches. Both engines issue their own bus requests.",
            "limits": "Bounded accepted instruction histories. Whole-game coverage and universal device timing require separate proofs.",
            "universal_game_equivalence": False,
        },
    )
    if not passed:
        raise ValueError("Warm wait-block comparison incomplete")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--documented", action="store_true", help="Admit all documented opcodes")
    qualify(documented=parser.parse_args().documented)
