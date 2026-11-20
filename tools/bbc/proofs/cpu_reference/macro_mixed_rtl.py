"""Compare C/RTL accepted edges with unrestricted NMI events from wait blocks."""

import json
import shutil
import subprocess
from pathlib import Path

from tools.bbc.proofs import memory_composition, nmi_macro
from tools.bbc.proofs.cpu_reference import mixed_rtl, nmi_sampling, prepared
from tools.bbc.proofs.cpu_reference.warm_checks import accepted_result
from tools.bbc.proofs.video_cursor import hashes, replace_once
from tools.validation.common import ROOT, write_json

DIRECTORY = ROOT / "build/bbc/cpu-source-macro-mixed-rtl"


def qualify():
    parents = [
        mixed_rtl.DIRECTORY / "result.json",
        nmi_sampling.DIRECTORY / "result.json",
        ROOT / "build/bbc/cpu-nmi-idle-summary/result.json",
        ROOT / "build/bbc/cpu-nmi-macro-transition-proof/result.json",
    ]
    for path in parents:
        memory_composition.sealed(path)
    DIRECTORY.mkdir(parents=True, exist_ok=True)
    core = ROOT / "build/bbc/ArletCpuEnabled.v"
    wrapper = ROOT / "rtl/bbc/BbcCpu.v"
    alu = ROOT / "third_party/6502/ALU.v"
    model, original = [mixed_rtl.DIRECTORY / name for name in ("source.v", "contract.v")]
    sampler = Path(__file__).with_name("nmi_sample.v")
    files = [
        Path(__file__),
        Path(nmi_macro.__file__),
        Path(mixed_rtl.__file__),
        Path(nmi_sampling.__file__),
        Path(prepared.__file__),
        Path(memory_composition.__file__),
        Path(__file__).with_name("warm_checks.py"),
        Path(__file__).parents[1] / "video_cursor.py",
        ROOT / "tools/validation/common.py",
        model,
        original,
        sampler,
        core,
        wrapper,
        alu,
        *parents,
        Path(shutil.which("yosys")),
    ]
    before = hashes(files)
    macro_core, macro_wrapper = nmi_macro.transform(core.read_text(), wrapper.read_text())
    sealed_macro = ROOT / "build/bbc/cpu-nmi-macro-transition-proof"
    if (sealed_macro / "macro-core.v").read_text() != macro_core or (
        sealed_macro / "macro-wrapper.v"
    ).read_text() != macro_wrapper:
        raise ValueError("Accepted-edge comparison uses a different proved macro CPU")
    body = replace_once(original.read_text(), "module MixedDirect(", "module MacroMixed(")
    body = replace_once(
        body, "reg fresh = 1;", "(* anyseq *) reg offEventInput,lastNmiInput;\nreg fresh = 1;"
    )
    body = replace_once(
        body,
        "wire [255:0] following;",
        """wire offEvent=phase==11 && offEventInput;
wire lastNmi=phase==11 && lastNmiInput;
wire [63:0] lastPins=(state[167:104]&~64'h8000000)|({63'd0,lastNmi}<<27);
wire [31:0] waitPipeline=state[79:48]|(offEvent ? 32'h100:32'd0);
wire [255:0] waited={state[255:168],lastPins,state[103:80],waitPipeline,state[47:0]};
wire [255:0] sampled,following;
M6502NmiSample sample(.state(waited),.nmi(nmi),.sampled(sampled));""",
    )
    body = replace_once(body, ".state_in(state)", ".state_in(sampled)")
    body = replace_once(
        body,
        ".clk(clk), .reset(fresh), .enable(!fresh)",
        ".offEvt(offEvent),.riseEvt(nmi && !lastNmi),\n        .clk(clk), .reset(fresh), .enable(!fresh)",
    )
    checks = []
    for name, text, correct in (
        ("positive", body, True),
        (
            "lost-source-wait-event",
            replace_once(body, "offEvent ? 32'h100:32'd0", "1'b0 ? 32'h100:32'd0"),
            False,
        ),
        ("lost-rtl-nmi-poll", body, False),
    ):
        case = DIRECTORY / name
        case.mkdir(exist_ok=True)
        contract, altered, wrapped = [
            case / filename for filename in ("contract.v", "cpu.v", "wrapper.v")
        ]
        contract.write_text(text)
        altered.write_text(
            replace_once(macro_core, "bbc_nmi_polled <= bbc_nmi_second;", "bbc_nmi_polled <= 0;")
            if name == "lost-rtl-nmi-poll"
            else macro_core
        )
        wrapped.write_text(macro_wrapper)
        graph, log, witness = [
            case / filename for filename in ("graph.json", "proof.log", "witness.json")
        ]
        prepared.prepare(
            case,
            f"read_verilog -formal -sv {model} {wrapped} {altered} {alu} {sampler} {contract}; prep -top MacroMixed -flatten; check -assert; async2sync; opt; memory_map; opt; techmap; opt; abc -g AND; opt_clean; write_json {graph}",
            [model, wrapped, altered, alu, sampler, contract],
            [graph],
        )
        module = json.loads(graph.read_text())["modules"]["MacroMixed"]
        if any(cell["type"] in ("$assume", "$check") for cell in module["cells"].values()):
            raise ValueError("Macro comparison acquired an assumption")
        prop = "equivalent" if correct else "faultDefined"
        witness.unlink(missing_ok=True)
        with log.open("w") as output:
            trial = subprocess.run(
                [
                    "yosys",
                    "-Q",
                    "-T",
                    "-p",
                    f"read_json {graph}; sat -set-init-zero -seq 36 -prove {prop} 1 -verify -timeout 300 -show {prop} -show offEventInput -show lastNmiInput -show irqInput -show nmiInput -dump_json {witness}",
                ],
                stdout=output,
                stderr=subprocess.STDOUT,
                timeout=420,
            )
        accepted = accepted_result(trial.returncode, log.read_text(), witness, prop, correct)
        checks.append({"name": name, "accepted": accepted})
        print("Macro mixed C/RTL " + name + (": PASS" if accepted else ": FAIL"), flush=True)
        if not accepted:
            break
    for path in parents:
        memory_composition.sealed(path)
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
            "scope": "36 accepted clocks, native cold startup then CLI/SEI/NOP, arbitrary response bytes and IRQ/NMI, and independent unrestricted wait-block rise/last-level summaries. The RTL macro is byte-identical to its all-168-bit actual transition theorem. Reference wait summaries preserve every instruction field, OR physical rises into the native source NMI latch, and retain the last level. Actual physical event combinations form a subset of this unrestricted domain. Every accepted bus request is compared without assumptions. Lost source wait capture and RTL polling faults require defined counterexamples.",
            "limits": "Bounded accepted-edge selected-instruction history, with quiet startup. Does not establish all-instruction/unbounded event histories, warm reset, physical timestamps, parent event ordering or whole-game equivalence.",
            "universal_game_equivalence": False,
        },
    )
    if not passed:
        raise ValueError("Macro mixed source/RTL comparison incomplete")


if __name__ == "__main__":
    qualify()
