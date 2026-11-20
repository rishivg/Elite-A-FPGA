"""Compose cold source/RTL histories and their physical NMI wait summaries."""

import json
import shutil
import subprocess
from pathlib import Path

from tools.bbc.proofs import memory_composition, nmi_macro
from tools.bbc.proofs.cpu_reference import (
    cold_lda_event_history,
    documented_model,
    event_history,
    foundation,
    history_recipe,
    lda_handoff,
    nmi_sampling,
    prepared,
    rtl_event_history,
    source_history,
)
from tools.bbc.proofs.video_cursor import hashes, replace_once
from tools.validation.common import ROOT, write_json

DIRECTORY = ROOT / "build/bbc/cpu-source-event-composition"
TOP = "SourceWaitSummary"


def audit_tree(paths):
    """Check every nested receipt, rather than trusting a hash of its verdict."""
    reports = {}

    def visit(path):
        path = path.resolve()
        if path in reports:
            return
        report = memory_composition.sealed(path)
        reports[path] = report
        for name in report["inputs"]:
            child = Path(name)
            child = child if child.is_absolute() else ROOT / child
            if child.name == "result.json":
                visit(child)

    for path in paths:
        visit(path)
    return reports


def observation_contract(source):
    """Remove only the C storage invariant and its observation-only ports."""
    marker = "wire [15:0] field_IR=cState[255:240];"
    if source.count(marker) != 1 or not source.endswith(
        "assign equivalent=observedEquivalent && (reset || stateRelation);\nendmodule\n"
    ):
        raise ValueError("Changed source observation implication")
    source = source[: source.index(marker)] + "assign equivalent=observedEquivalent;\nendmodule\n"
    source = replace_once(
        source,
        "(* anyconst *) reg [7:0] a,x,y,s,p,firstOpcode;",
        "(* anyconst *) reg [7:0] firstOpcode;\nwire [7:0] a=8'h40,x=0,y=0,s=8'hfd,p=4;",
    )
    source = replace_once(
        source, "(* anyconst *) reg seedNmi,seedPrevious;", "wire seedNmi=0,seedPrevious=0;"
    )
    source = replace_once(
        source, "(* anyconst *) reg [4:0] seedOlderIrq;", "wire [4:0] seedOlderIrq=0;"
    )
    source = replace_once(
        source, ".seedNmi(seedNmi),.seedPrevious(seedPrevious),.seedOlderIrq(seedOlderIrq),", ""
    )
    return replace_once(
        source,
        ",.proofResponse(cResponse),.proofState(cState),.proofPins(cPins),.proofHistory(cHistory),.proofPreviousFetch(cPreviousFetch)",
        "",
    )


def check_recipes(initial, pins):
    source_prefix, source_contract = event_history.recipe(initial, pins)
    rtl_prefix, rtl_contract = cold_lda_event_history.recipe(initial, pins)
    for directory, prefix, contract in (
        (event_history.DIRECTORY, source_prefix, source_contract),
        (cold_lda_event_history.DIRECTORY, rtl_prefix, rtl_contract),
    ):
        if (directory / "prefix.v").read_text() != prefix or (
            directory / "contract.v"
        ).read_text() != contract:
            raise ValueError("Sealed proof differs from its maintained recipe: " + str(directory))
    if observation_contract(source_contract) != rtl_contract:
        raise ValueError("Cold RTL and source have different observation contracts")
    if (
        source_contract.count("always @* assume(documented(firstOpcode)&&documented(nextOpcode));")
        != 1
    ):
        raise ValueError("Changed official-opcode assumption")
    # The handoff proves equality to this exact initializer, including the
    # hidden microstep, saved pins and first opcode response, not just A/X/Y/P.
    gate = lda_handoff.gate_contract(
        {"default_native_state": initial, "default_native_pins": pins},
        (ROOT / "build/bbc/cpu-source-mixed-rtl/preservation.v").read_text(),
    )
    if (lda_handoff.DIRECTORY / "gate-profile/contract.v").read_text() != gate:
        raise ValueError("Cold handoff uses a different source initializer")
    macro = nmi_macro.transform(
        (ROOT / "build/bbc/ArletCpuEnabled.v").read_text(),
        (ROOT / "rtl/bbc/BbcCpu.v").read_text(),
    )
    producer = ROOT / "build/bbc/cpu-nmi-macro-transition-proof"
    if any(
        (producer / name).read_text() != text
        for name, text in zip(("macro-core.v", "macro-wrapper.v"), macro, strict=True)
    ):
        raise ValueError("Physical transition uses a different RTL event macro")
    actual = macro[0] + "\n" + macro[1] + "\n" + (ROOT / "third_party/6502/ALU.v").read_text()
    if (cold_lda_event_history.DIRECTORY / "actual.v").read_text() != actual:
        raise ValueError("Cold history uses a different RTL event macro")
    return source_prefix


def summary_contract(prefix):
    # Use the executing source theorem's projection verbatim. Its only
    # specialization is the post-entry, NMI-enabled profile and named inputs.
    start = prefix.index("  wire [31:0] eventPipeline=")
    end = prefix.index("  M6502ReferenceNext source(", start)
    projection = prefix[start:end]
    for old, new in (
        ("referenceState", "initialState"),
        ("age>=26", "1'b1"),
        ("ALLOW_NMI", "1'b1"),
        ("offNmiPin", "offEvent"),
        ("riseNmiPin", "freshRise"),
        ("nmiPin", "acceptedNmi"),
    ):
        if old not in projection:
            raise ValueError("Changed source event projection: " + old)
        projection = projection.replace(old, new)
    return (
        """module SourceWaitSummary(input clk,output equivalent);
(* anyconst *) reg [255:0] initialState;
(* anyseq *) reg nmi,acceptedNmi;
reg first=1;
reg [255:0] waited=0;
reg previous=0,offEvent=0;
wire [255:0] sampled,accepted;
M6502NmiSample waitSample(.state(waited),.nmi(nmi),.sampled(sampled));
M6502NmiSample finalSample(.state(waited),.nmi(acceptedNmi),.sampled(accepted));
always @(posedge clk)begin
 first<=0;
 if(first)begin waited<=initialState;previous<=initialState[131];offEvent<=0;end
 else begin
  waited<=sampled;
  previous<=nmi;
  offEvent<=offEvent || (nmi && !previous);
 end
end
wire freshRise=acceptedNmi && !previous;
wire [63:0] waitPins=(initialState[167:104]&~64'h8000000)|({63'd0,previous}<<27);
wire [31:0] waitPipeline=initialState[79:48]|(offEvent ? 32'h100:32'd0);
wire [255:0] waitState={initialState[255:168],waitPins,
 initialState[103:80],waitPipeline,initialState[47:0]};
"""
        + projection
        + "assign equivalent=first || (waited==waitState && accepted==eventState);\nendmodule\n"
    )


def guard_summary(graph):
    module = graph["modules"][TOP]
    if any(cell["type"] in {"$assume", "$check"} for cell in module["cells"].values()):
        raise ValueError("Source wait summary acquired an assumption")
    for name, width, kind in (
        ("initialState", 256, "$anyconst"),
        ("nmi", 1, "$anyseq"),
        ("acceptedNmi", 1, "$anyseq"),
    ):
        wire = module["netnames"][name]
        bits = wire["bits"]
        drivers = [
            cell
            for cell in module["cells"].values()
            if cell["type"] == kind and cell["connections"]["Y"] == bits
        ]
        if len(bits) != width or "init" in wire["attributes"] or len(drivers) != 1:
            raise ValueError("Source wait summary narrowed its input: " + name)


def prove_summary(prefix):
    body = summary_contract(prefix)
    sampler = Path(nmi_sampling.__file__).with_name("nmi_sample.v")
    checks, artifacts = [], []
    for name, text, positive in (
        ("positive", body, True),
        (
            "lost-wait-event",
            replace_once(body, "(offEvent || freshRise)", "freshRise"),
            False,
        ),
        (
            "wrong-final-level",
            replace_once(body, "1'b1 && 1'b1 && acceptedNmi", "1'b0"),
            False,
        ),
    ):
        case = DIRECTORY / name
        case.mkdir(parents=True, exist_ok=True)
        contract, graph, log, witness = [
            case / filename
            for filename in ("contract.v", "graph.json", "proof.log", "witness.json")
        ]
        contract.write_text(text)
        prepared.prepare(
            case,
            f"read_verilog -formal -sv {sampler} {contract}; prep -top {TOP} -flatten; check -assert; opt; write_json {graph}",
            [sampler, contract],
            [graph],
        )
        guard_summary(json.loads(graph.read_text()))
        witness.unlink(missing_ok=True)
        horizon = "-tempinduct -initsteps 2 -maxsteps 8" if positive else "-seq 4"
        with log.open("w") as output:
            trial = subprocess.run(
                [
                    "yosys",
                    "-Q",
                    "-T",
                    "-p",
                    f"read_json {graph}; sat -set-init-zero {horizon} -prove equivalent 1 -verify -timeout 60 -show equivalent -show nmi -show acceptedNmi -show offEvent -dump_json {witness}",
                ],
                stdout=output,
                stderr=subprocess.STDOUT,
                timeout=90,
            )
        output = log.read_text()
        accepted = (
            trial.returncode == 0 and "Induction step proven: SUCCESS!" in output
            if positive
            else trial.returncode != 0
            and "ERROR: Called with -verify and proof did fail!" in output
            and witness.is_file()
            and any(
                signal["name"] == "equivalent" and "0" in signal.get("wave", "")
                for signal in json.loads(witness.read_text())["signal"]
            )
        )
        checks.append({"name": name, "accepted": accepted})
        artifacts.extend([contract, graph, log, case / "prepared.json", case / "prepare.log"])
        if witness.is_file():
            artifacts.append(witness)
        print("Source wait composition " + name + (": PASS" if accepted else ": FAIL"), flush=True)
        if not accepted:
            raise ValueError("Source wait composition failed: " + name)
    return checks, artifacts


def qualify():
    parents = {
        module.__name__.rsplit(".", 1)[-1]: module.DIRECTORY / "result.json"
        for module in (
            foundation,
            source_history,
            event_history,
            lda_handoff,
            cold_lda_event_history,
            nmi_sampling,
        )
    }
    parents.update(
        idle=ROOT / "build/bbc/cpu-nmi-idle-summary/result.json",
        accepted=ROOT / "build/bbc/cpu-nmi-macro-transition-proof/result.json",
    )
    tree = audit_tree(parents.values())
    reports = {name: tree[path.resolve()] for name, path in parents.items()}
    if reports["event_history"]["opcodes"] != documented_model.inventory():
        raise ValueError("Source event history narrowed its opcode domain")
    if reports["event_history"]["seeds"] != {"seed_bits": 7, "seed_combinations": 128}:
        raise ValueError("Source event history narrowed its entry domain")
    for name in ("idle", "accepted"):
        if (
            reports[name].get("register_bits") != 168
            or reports[name].get("excluded_register_bits") != 0
        ):
            raise ValueError("Incomplete physical CPU state inventory")
    if reports["accepted"].get("inactive_nmi_initialization_all_dummy_phases") is not True:
        raise ValueError("Missing cold initialization transition theorem")
    files = [
        Path(__file__),
        *tree,
        *history_recipe.producer_files(),
        *[
            Path(module.__file__)
            for module in (
                cold_lda_event_history,
                documented_model,
                event_history,
                foundation,
                lda_handoff,
                memory_composition,
                nmi_macro,
                nmi_sampling,
                prepared,
                rtl_event_history,
                source_history,
            )
        ],
        Path(__file__).parents[1] / "video_cursor.py",
        ROOT / "tools/validation/common.py",
        Path(shutil.which("yosys")),
    ]
    before = hashes(files)
    prefix = check_recipes(
        reports["foundation"]["default_native_state"],
        reports["foundation"]["default_native_pins"],
    )
    checks, artifacts = prove_summary(prefix)
    # Freshly execute the sealed complete-state handoff SAT query. Do not
    # overwrite its original evidence or turn preparation reuse into a pass.
    entry_graph = lda_handoff.DIRECTORY / "gate-profile/graph.json"
    entry_module = json.loads(entry_graph.read_text())["modules"]["NativeLdaEntry"]
    if any(cell["type"] in {"$assume", "$check"} for cell in entry_module["cells"].values()):
        raise ValueError("Cold source handoff acquired an assumption")
    entry_log = DIRECTORY / "entry-proof.log"
    with entry_log.open("w") as output:
        trial = subprocess.run(
            [
                "yosys",
                "-Q",
                "-T",
                "-p",
                f"read_json {entry_graph}; sat -prove equivalent 1 -verify -timeout 120",
            ],
            stdout=output,
            stderr=subprocess.STDOUT,
            timeout=180,
        )
    if trial.returncode != 0 or "no model found: SUCCESS!" not in entry_log.read_text():
        raise ValueError("Fresh cold source handoff failed")
    artifacts.append(entry_log)
    audit_tree(parents.values())
    stable = before == hashes(files)
    report = {
        "passed": stable,
        "inputs_unchanged": stable,
        "inputs": before,
        "artifacts": hashes(artifacts),
        "premises": {name: str(path.relative_to(ROOT)) for name, path in parents.items()},
        "audited_receipts": len(tree),
        "checks": checks,
        "fresh_complete_state_entry_query": True,
        "opcodes": documented_model.inventory(),
        "instruction_count": "unbounded",
        "derivation": [
            "The independently checked C constructor and nine real source transitions reach the exact generalized history initializer after seven reset slots and LDA #$40, for every vector and discarded byte. The fresh handoff query compares every state/pin bit and the first response.",
            "The maintained source event theorem and actual cold RTL macro theorem use byte-identical independent bus/retirement specifications after substituting the proved concrete entry registers and zero interrupt seeds. Both keep the same official-opcode assumption and unrestricted response/event inputs. Their deterministic observer histories therefore coincide after entry.",
            "Fresh initialized induction proves that any number of exact native-source NMI samples, followed by acceptance sampling, equals the source event theorem's own full-256-bit projection. It preserves all other state and pipeline age. Both dropped wait events and wrong final pin levels require counterexamples.",
            "The physical RTL disabled-edge theorem and exact all-168-bit accepted-edge theorem transfer that same event summary to actual enabled execution. The macro implementation is byte-identical across the history and physical transition proofs. Quiet startup uses the all-dummy-phase initialization lemma; waits may be inserted without advancing instructions.",
            "Induction over accepted edges consequently gives equal accepted bus addresses, read/write direction, written bytes and specified retirement observations for the source and physical RTL under matching responses, IRQ levels and NMI event histories in the declared domain.",
        ],
        "scope": "Conditional cold-start C/physical-RTL CPU bus and retirement equivalence for unbounded official-instruction histories, arbitrary inserted disabled edges and NMI events. Native source construction, exact post-LDA entry, common observations and both physical wait projections are bound to recursively audited evidence. The source wait-summary induction and complete-state entry SAT query execute freshly.",
        "limits": "Declared zero-initialized RTL power-on profile; quiet IRQ/NMI through cold reset and LDA #$40; matching accepted response bytes and event streams; official fetched opcodes; no later reset/warm requests. Parent/device callback ordering and physical timestamps, actual OS response/interrupt and opcode eligibility, remaining device/video composition and whole-game/board equivalence are separate obligations. Existing all-instruction history and translation premises are audited, not rerun by this target.",
        "universal_cpu_equivalence": False,
        "universal_game_equivalence": False,
    }
    write_json(DIRECTORY / "result.json", report)
    if not stable:
        raise ValueError("CPU event composition inputs changed")
    print("Cold source/physical-RTL event composition: PASS", flush=True)
    return report


if __name__ == "__main__":
    qualify()
