"""Actual all-state reset cancellation and quiet closure; original control matrices."""

import json
import re
import shutil
import subprocess
from pathlib import Path

from tools.bbc.proofs import current_machine
from tools.bbc.proofs.cpu_reference import callback_cpu, callback_order, prepared
from tools.bbc.proofs.video_cursor import hashes, replace_once
from tools.validation.common import ROOT, source_root, write_json

DIRECTORY = ROOT / "build/bbc/fdc-reset-closure"
FIELDS = {
    "phase_1": 4,
    "mechanicalPhase": 2,
    "dispatchPending": 1,
    "acceptingParameters": 1,
    "specifying": 1,
    "readDataPending": 1,
    "indexCallback": 2,
    "driveOut": 8,
    "busy": 1,
    "interrupt": 1,
    "request": 1,
    "nmiLevel": 1,
    "mode": 8,
    "readyTimeout": 24,
    "readyPulses": 2,
    "ready": 1,
    "result": 8,
    "data": 8,
}
QUIET = tuple(FIELDS)[:13]


def fields(body):
    state = body["ports"]["proofState"]["bits"]
    following = body["ports"]["proofNext"]["bits"]
    if len(state) != 934 or len(set(state)) != 934 or len(following) != 934:
        raise ValueError("Incomplete reset controller state")
    result, used = {}, set()
    for name, width in FIELDS.items():
        bits = body["netnames"][name]["bits"]
        if len(bits) != width or not set(bits).issubset(state) or used.intersection(bits):
            raise ValueError("Missing, aliased or narrowed reset field: " + name)
        used.update(bits)
        result[name] = [state.index(bit) for bit in bits]
    return result


def contract(body, enable):
    inventory = fields(body)

    def value(vector, name):
        return "{" + ",".join(f"{vector}[{i}]" for i in reversed(inventory[name])) + "}"

    def zero(vector, names):
        return " && ".join(value(vector, name) + "==0" for name in names)

    inputs, connections = [], []
    for name, port in body["ports"].items():
        if port["direction"] == "input":
            inputs.append(f"input[{len(port['bits']) - 1}:0] {name}")
            connections.append(f".{name}({name})")
    preserved = " && ".join(
        value("following", name) + "==" + event
        for name, event in (
            ("ready", "eventReady"),
            ("result", "eventResult"),
            ("data", "eventData"),
        )
    )
    return f"""module FdcResetClosure({",".join(inputs)},output equivalent,resetEquivalent,quietEquivalent);
wire[933:0] following;
wire pin,directEvent,callbackEvent,commandEvent,abortEvent,eventReady;
wire[7:0]eventResult,eventData;
FdcStateNext actual({",".join(connections)},.proofNext(following),
 .io_nmi(pin),.io_nmiEvent(directEvent),.proofEvent(callbackEvent),
 .proofCommand(commandEvent),.proofAbort(abortEvent),
 .resetReady(eventReady),.resetResult(eventResult),.resetData(eventData));
wire enabled={enable};
// A public reset write is one bus operation. A warm reset here has no host access.
wire resetAccess=!io_read && ((io_write && io_address==2 && io_data==1) ||
 (io_warmReset && !io_write));
wire quiet={zero("proofState", QUIET)};
wire nextQuiet={zero("following", QUIET)};
wire noCommand=!(io_write && io_address==0);
assign resetEquivalent=reset || !enabled || !resetAccess ||
 (nextQuiet && {zero("following", ("readyTimeout", "readyPulses"))} && {preserved});
assign quietEquivalent=reset || !quiet || !noCommand ||
 (nextQuiet && !pin && !directEvent && !callbackEvent && !commandEvent && !abortEvent);
assign equivalent=resetEquivalent && quietEquivalent && (enabled || following==proofState);
endmodule
"""


def reset_variants(source):
    if source.count("localparam Phase_Idle = 4'd0;") != 1:
        raise ValueError("Changed actual idle phase encoding")
    pattern = r"if\([^\n]+\) begin\n(?P<indent> +)readDataPending <= 1'b0;\n(?P=indent)acceptingParameters <= 1'b0;\n(?:(?P=indent)specifying <= 1'b0;\n)?(?P=indent)rotation <= [^;]+;\n(?P=indent)phase_1 <= Phase_Idle;"
    matches = list(re.finditer(pattern, source))
    if len(matches) != 1:
        raise ValueError("Changed actual reset cancellation block")
    block = matches[0][0]
    indent = matches[0]["indent"]
    mode_clear = "readyPulses <= 2'b00;\n" + indent + "mode <= 8'h0;"
    return [
        ("positive", source),
        (
            "callback-overrides-reset-mode",
            replace_once(source, mode_clear, "readyPulses <= 2'b00;"),
        ),
        (
            "stale-phase",
            replace_once(
                source, block, replace_once(block, "phase_1 <= Phase_Idle;", "phase_1 <= phase_1;")
            ),
        ),
        (
            "stale-parameters",
            replace_once(
                source,
                block,
                replace_once(
                    block,
                    "acceptingParameters <= 1'b0;",
                    "acceptingParameters <= acceptingParameters;",
                ),
            ),
        ),
    ]


def rtl_proofs():
    checks, artifacts = [], []
    for multiplier, parent, top in current_machine.parents():
        controller, extra = callback_order.fdc_instance(parent.read_text(), top)
        for name, source in reset_variants(controller):
            case = DIRECTORY / f"{name}{multiplier}"
            case.mkdir(parents=True, exist_ok=True)
            model, raw, cut, wrapper, graph = [
                case / n for n in ("actual.v", "raw.json", "cut.json", "contract.v", "graph.json")
            ]
            model.write_text(
                re.sub(
                    r'\$readmemb\("([^\"]+)"',
                    lambda m: '$readmemb("' + str(parent.parent / m[1]) + '"',
                    source,
                )
            )
            prepared.prepare(
                case,
                f"read_verilog -D SYNTHESIS {model}; hierarchy -top Intel8271; proc; memory_map; opt; dffunmap; check -assert; write_json {raw}",
                [model],
                [raw],
            )
            data = callback_cpu.cut_fdc(json.loads(raw.read_text()))
            body = data["modules"]["FdcStateNext"]
            for port, wire in (
                ("resetReady", "eventReady"),
                ("resetResult", "eventResult"),
                ("resetData", "eventData"),
            ):
                body["ports"][port] = {
                    "direction": "output",
                    "bits": body["netnames"][wire]["bits"],
                }
            write_json(cut, data)
            wrapper.write_text(contract(body, extra[0] if extra else "1'b1"))
            logs = callback_cpu.prove(
                case,
                f"read_json {cut}; read_verilog {wrapper}; prep -top FdcResetClosure -flatten; opt; check -assert; write_json {graph}",
                "equivalent" if name == "positive" else "resetEquivalent",
                name == "positive",
                horizon=0,
                show="-show-inputs -show quiet -show nextQuiet -show resetAccess",
            )
            compiled = json.loads(graph.read_text())
            callback_cpu.guard_inputs(
                compiled,
                "FdcResetClosure",
                {n: len(p["bits"]) for n, p in body["ports"].items() if p["direction"] == "input"},
            )
            checks.append(
                {
                    "clock_multiplier": multiplier,
                    "name": name,
                    "accepted": True,
                    "register_bits": 934,
                }
            )
            artifacts.extend(
                [
                    model,
                    raw,
                    cut,
                    wrapper,
                    graph,
                    case / "prepared.json",
                    case / "prepare.log",
                    *logs,
                ]
            )
    return checks, artifacts


def qualify():
    current_machine.audit()
    DIRECTORY.mkdir(parents=True, exist_ok=True)
    reference = source_root("jsbeeb")
    helper = Path(__file__).with_suffix(".mjs")
    files = [
        Path(__file__),
        helper,
        ROOT / "rtl/bbc/Intel8271.scala",
        current_machine.DIRECTORY / "generation.json",
        *[ROOT / name for name in current_machine.generation_inputs()],
        *[p for _, p, _ in current_machine.parents()],
        *[blob for _, p, _ in current_machine.parents() for blob in p.parent.glob("*.bin")],
        *sorted((reference / "src").rglob("*.js")),
        *[Path(m.__file__) for m in (current_machine, callback_cpu, callback_order, prepared)],
        ROOT / "tools/bbc/proofs/video_cursor.py",
        ROOT / "tools/validation/common.py",
        *[Path(shutil.which(tool)) for tool in ("node", "yosys")],
    ]
    before = hashes(files)
    original = DIRECTORY / "original.json"
    subprocess.run(["node", str(helper), str(reference), str(original)], check=True, timeout=60)
    report = json.loads(original.read_text())
    if report != {
        "passed": True,
        "control_cases": 2700,
        "status_cases": 512,
        "drive_cases": 192,
        "dispatch_cases": 2048,
        "quiet_cases": 2048,
        "inert_timers": 450,
    }:
        raise ValueError("Incomplete original reset projection inventory")
    checks, artifacts = rtl_proofs()
    current_machine.audit()
    stable = before == hashes(files)
    write_json(
        DIRECTORY / "result.json",
        {
            "passed": stable,
            "inputs_unchanged": stable,
            "inputs": before,
            "artifacts": hashes([original, *artifacts]),
            "checks": checks,
            "source": report,
            "scope": "Fresh all-state combinational SAT over all 934 actual FDC Q/D bits at 32/96 MHz. Each enabled public register-2 reset (no simultaneous read) or access-free warm reset establishes a quiet control invariant and clears ready timers. Callback-stage result, data and result-ready survive. Every non-command host operation, arbitrary storage and arbitrary old timer/counter/register values preserve the quiet invariant and emit no pin or explicit NMI event; disabled state holds. Reset establishment plus closure gives arbitrary-length quiet RTL epochs, including repeated reset, until a command write. Original maintained code executes exhaustive finite control, status, valid drive-output, host-dispatch and idle-operation matrices, including queued inert timers.",
            "limits": "The original matrices are finite executions with seeded source-private control projections, not an unbounded JavaScript translation or full source/RTL state correspondence. No DUT simulation state is seeded. Reset may retain a callback-stage completion on its own edge; quiet starts after the enabled reset edge. An original timer with no callback label can remain queued and is inert for NMI, though it clears registers 8/9. Source drive states with a write gate and no selected drive are undefined and excluded. Commands end the quiet epoch. Arbitrary command/retry/media event correspondence, accepting CPU reset events, arbitrary CPU warm-reset histories and whole-game/board equivalence remain open.",
            "universal_game_equivalence": False,
        },
    )
    if not stable:
        raise ValueError("Reset proof inputs changed")


if __name__ == "__main__":
    qualify()
