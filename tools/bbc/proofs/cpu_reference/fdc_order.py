"""Order actual millisecond and index callbacks at a shared source epoch."""

import json
import re
import shutil
import subprocess
from pathlib import Path

from tools.bbc import bus_binding_proof, cpu_events, cpu_interrupt_proof
from tools.bbc.proofs import current_machine, memory_composition
from tools.bbc.proofs.cpu_reference import (
    callback_cpu,
    callback_order,
    fdc_epoch,
    fdc_events,
    prepared,
)
from tools.bbc.proofs.video_cursor import hashes, replace_once
from tools.validation.common import ROOT, source_root, write_json

DIRECTORY = ROOT / "build/bbc/fdc-callback-order"
NAMES = (
    [
        f"{op}-{head}-{offset}"
        for op in (105, 83, 91)
        for head in (0, 16, 32, 240)
        for offset in (-1, 0, 1)
    ]
    + [f"special-{address}" for address in range(64)]
    + ["special-clock-temporary", "rephased-byte-ack"]
)
FAULTS = {
    "stale-selector": ("105-240-0", "orderEquivalent"),
    "stale-count": ("83-240-0", "orderEquivalent"),
    "late-first-timer": ("105-240-0", "initialDelayEquivalent"),
    "lost-special-ready": ("105-0--1", "specialReadyEquivalent"),
    "stale-clock-temporary": ("special-clock-temporary", "specialReadyEquivalent"),
    "held-byte-deadline": ("rephased-byte-ack", "rephaseEquivalent"),
    "post-ack-nmi": ("rephased-byte-ack", "ackEquivalent"),
}

FIELDS = {
    "phase_1": 4,
    "mechanicalPhase": 2,
    "mechanicalDelay": 24,
    "mechanicalOp": 4,
    "dispatchPending": 1,
    "parameters_1": 8,
    "delay": 24,
    "idSector": 8,
    "idTrack": 8,
    "executingOp": 4,
    "indexCallback": 2,
    "indexCount": 8,
    "headRate": 8,
    "driveOut": 8,
    "headerPointer": 8,
    "result": 8,
    "stepRate": 8,
    "command": 8,
    "parameters_0": 8,
    "register0": 8,
    "readDataPending": 1,
    "mode": 8,
    "request": 1,
    "nmiLevel": 1,
    "nmiGap": 1,
    "interrupt": 1,
}


def indices(body):
    state = body["ports"]["proofState"]["bits"]
    if (
        len(state) != 934
        or len(set(state)) != 934
        or len(body["ports"]["proofNext"]["bits"]) != 934
    ):
        raise ValueError("Incomplete timer/index controller state")
    fields, used = {}, set()
    for name, width in FIELDS.items():
        bits = body["netnames"][name]["bits"]
        if len(bits) != width or not set(bits).issubset(state) or used.intersection(bits):
            raise ValueError("Changed timer/index state: " + name)
        fields[name] = [state.index(bit) for bit in bits]
        used.update(bits)
    return fields


def contract(body, enable, rates):
    fields = indices(body)

    def q(n, v="proofState"):
        return "{" + ",".join(f"{v}[{i}]" for i in reversed(fields[n])) + "}"

    inputs = []
    ports = []
    for n, p in body["ports"].items():
        if p["direction"] == "input":
            inputs.append(f"input[{len(p['bits']) - 1}:0] {n}")
            ports.append(f".{n}({n})")
    table = "\n".join(f"8'd{i}:sourceStep=24'd{ms * 32000};" for i, ms in enumerate(rates))
    return f"""module TimerIndexOrder({",".join(inputs)},output orderEquivalent,initialDelayEquivalent,specialReadyEquivalent,rephaseEquivalent,ackEquivalent);
wire[933:0] following;wire pulse,zero,expiry;wire[23:0]postDelay;wire[7:0]pins;wire retained;
wire resultReady,commandBusy,writeSpecial,pin,cellBoundaryEvent;wire[7:0]readValue,busValue;
FdcStateNext actual({",".join(ports)},.proofNext(following),.order_pulse(pulse),.order_zero(zero),.order_post(postDelay),.order_pins(pins),.order_latched(retained),.order_expiry(expiry),.order_ready(resultReady),.order_busy(commandBusy),.order_result(readValue),.order_writeSpecial(writeSpecial),.order_cell(cellBoundaryEvent),.io_nmi(pin),.io_result(busValue));
wire[3:0]phase={q("phase_1")},owner={q("executingOp")};
wire[1:0]mech={q("mechanicalPhase")};wire[3:0]mechanicalOwner={q("mechanicalOp")};
wire finishing={q("mechanicalDelay")}==0 && (mech==2 || (mech==1 && postDelay==0 && ({q("idSector")}==0 || ({q("idTrack")}==0 && zero))));
wire[7:0]drive={q("driveOut")},head={q("headRate")};wire[3:0]unload=head[7:4];
wire domain={enable} && !reset && !io_read && !io_write && !io_warmReset && phase==0 && !{q("dispatchPending")} && pulse && finishing && drive[7:6]==1 && drive[3] && retained && pins[2] && (mechanicalOwner<=7 || mechanicalOwner==10);
wire seek=mechanicalOwner==10,id=mechanicalOwner==6;
wire[7:0]expectedCount=seek?((unload==0 || unload==15)?{q("indexCount")}:{{4'd0,unload}}-8'd1):(id?8'd3:8'd2);
wire[1:0]expectedKind=seek?((unload==0 || unload==1 || unload==15)?2'd0:2'd2):2'd1;
wire[7:0]expectedDrive=seek && (unload==0 || unload==1)?drive & 8'h37:drive;
assign orderEquivalent=!domain || ({q("indexCount", "following")}==expectedCount && {q("indexCallback", "following")}==expectedKind && {q("result", "following")}=={q("result")} && {q("phase_1", "following")}==(seek?4'd0:4'd7) && {q("headerPointer", "following")}==(seek?8'd0:8'd12) && {q("driveOut", "following")}==expectedDrive && !expiry);
function[23:0]sourceStep(input[7:0]rate);begin case(rate)
{table}
endcase end endfunction
wire initialDomain={enable} && !reset && !io_read && !io_write && !io_warmReset && phase==0 && {q("dispatchPending")} && (cmd[5:2]<=7 || cmd[5:2]==10) && !(cmd[5:2]==6 && {q("parameters_1")}!=0) && {q("mechanicalPhase", "following")}==1;
assign initialDelayEquivalent=!initialDomain || {q("mechanicalDelay", "following")}==sourceStep({q("stepRate")})-24'd17;
wire[7:0]cmd={q("command")};
wire specialDomain={enable} && !reset && !io_read && !io_write && !io_warmReset && !pulse && phase==0 && {q("dispatchPending")} && cmd[5:2]==15;
wire[7:0]registerSelect={q("parameters_0")};
assign specialReadyEquivalent=!specialDomain || (resultReady && !commandBusy && {q("phase_1", "following")}==0 && (registerSelect[5:0]!=36 || readValue=={{drive[7:6],6'd0}}));
wire[7:0]ptr={q("register0")};
wire readPayload=!{q("dispatchPending")} && (mech==0 || {q("mechanicalDelay")}>0) && phase==8 && owner==4 && !{q("readDataPending")};
wire rephaseDomain={enable} && !reset && !io_warmReset && !pulse && readPayload &&
 io_write && !io_read && io_address==1 && writeSpecial && registerSelect[5:0]==35 &&
 ptr[5:0]==6 && {q("delay")}>16 && drive[7:6]==1 && drive[3] && io_data[3] && io_data[7:6]==drive[7:6];
assign rephaseEquivalent=!rephaseDomain || ({q("delay", "following")}==15 && {q("phase_1", "following")}==8);
wire[7:0]md={q("mode")};
wire ackDomain={enable} && !reset && !io_warmReset && !pulse && cellBoundaryEvent && readPayload &&
 io_read && !io_write && io_address==4 && {q("delay")}==0 && md[5:4]==3 && md[0] &&
 !{q("request")} && !{q("nmiLevel")} && !{q("nmiGap")} && cmd[5:2]>1 && io_storageData[15:8]==255;
assign ackEquivalent=!ackDomain || (pin && busValue==io_storageData[7:0] &&
 !{q("nmiLevel", "following")} && !{q("request", "following")} && !{q("interrupt", "following")});
endmodule
"""


def fault(source, name):
    if name == "held-byte-deadline":
        matches = re.findall(r"(?m)^  assign readByteRephase = [^;]+;$", source)
        if len(matches) != 1:
            raise ValueError("Changed read-byte rephase target")
        return replace_once(source, matches[0], "  assign readByteRephase = 1'b0;")
    if name == "post-ack-nmi":
        return replace_once(
            source,
            "assign io_nmi = (((eventNmi || commandNmi) && (! io_warmReset)) && (! nmiGap));",
            "assign io_nmi = ((hostNmi && (! io_warmReset)) && (! nmiGap));",
        )
    if name == "stale-clock-temporary":
        matches = re.findall(r"(?m)^  assign dispatchClocks = [^;]+;$", source)
        if len(matches) != 1:
            raise ValueError("Changed dispatch clock temporary target")
        return replace_once(source, matches[0], "  assign dispatchClocks = mmioClocks;")
    if name == "lost-special-ready":
        matches = re.findall(r"(?m)^  assign readSpecialNow = [^;]+;$", source)
        if len(matches) != 1:
            raise ValueError("Changed immediate READ SPECIAL target")
        return replace_once(source, matches[0], "  assign readSpecialNow = 1'b0;")
    if name == "late-first-timer":
        return replace_once(source, "stepDelay - 24'h000011", "stepDelay - 24'h000002")
    field, expected = {
        "stale-selector": ("preIndexCallback", 4),
        "stale-count": ("preIndexCount", 5),
    }[name]
    original = "indexCallback" if name == "stale-selector" else "indexCount"
    count = 0

    def consumer(match):
        nonlocal count
        prefix, lhs, operator, rhs = match.groups()
        if lhs != field and re.search(r"\b" + field + r"\b", rhs):
            count += 1
            rhs = re.sub(r"\b" + field + r"\b", original, rhs)
        return prefix + lhs + operator + rhs + ";"

    changed = re.sub(r"(?m)^(\s*(?:assign )?)(\w+)(\s*(?:<=|=)\s*)([^;\n]+);", consumer, source)
    if count != expected:
        raise ValueError("Changed pre-index consumer inventory: " + name)
    return changed


def original(path):
    report = json.loads(path.read_text())
    cases, rates = report.get("cases", []), report.get("step_rates", [])
    if (
        report.get("passed") is not True
        or [case.get("name") for case in cases] != NAMES
        or len(rates) != 256
        or any(type(rate) is not int or not 1 <= rate <= 510 for rate in rates)
        or any(case.get("deadline") != report.get("deadline") for case in cases)
        or any(case.get("tie") is not (case["name"] in NAMES[:36]) for case in cases)
        or any(type(case.get("rows")) is not int or case["rows"] <= 0 for case in cases)
    ):
        raise ValueError("Incomplete original timer/index projection")
    deadline = report["deadline"]
    rephased = cases[-1]
    if rephased.get("edges") != 2 or rephased.get("next") != rephased.get("rephase", -2) + 1:
        raise ValueError("Missing original rephased byte/acknowledgement edge")
    for case in cases:
        if case["name"].endswith("-0") and case.get("tie") is True:
            expected = [[deadline, "timer"]]
            if case["name"] != "105-0-0":
                expected.append([deadline, "index"])
            if case.get("events") != expected:
                raise ValueError("Original scheduler did not order timer before index")
    return cases, rates


def formal(multiplier, parent, top, rates):
    controller, extra = callback_order.fdc_instance(parent.read_text(), top)
    checks, artifacts = [], []
    variants = [("positive", controller, None)] + [
        (name, fault(controller, name), prop) for name, (_, prop) in FAULTS.items()
    ]
    for name, text, prop in variants:
        case = DIRECTORY / f"formal-{name}{multiplier}"
        case.mkdir(exist_ok=True)
        model, raw, cut, wrapper, graph = [
            case / name for name in ("actual.v", "raw.json", "cut.json", "contract.v", "graph.json")
        ]
        if name == "post-ack-nmi":
            # The historical pin fault disconnects the command stage. Retain
            # that existing wire through an observation-only output so the
            # unchanged full-state cutter can still inventory every stage.
            text = replace_once(
                text, "module Intel8271 (", "module Intel8271 (output proofKeepCommand,"
            )
            text = replace_once(text, "endmodule", "assign proofKeepCommand=commandNmi;\nendmodule")
        model.write_text(
            re.sub(
                r'\$readmemb\("([^\"]+)"',
                lambda m: '$readmemb("' + str(parent.parent / m[1]) + '"',
                text,
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
        for port, signal in {
            "pulse": "index",
            "zero": "calibrationAtZero",
            "post": "seekPostDelay",
            "pins": "driveIn",
            "latched": "latchedReady",
            "expiry": "indexExpires",
            "ready": "eventReady",
            "busy": "eventBusy",
            "result": "eventResult",
            "writeSpecial": "writeSpecialStarts",
            "cell": "cellBoundary",
        }.items():
            body["ports"]["order_" + port] = {
                "direction": "output",
                "bits": body["netnames"][signal]["bits"],
            }
        write_json(cut, data)
        wrapper.write_text(contract(body, extra[0] if extra else "1'b1", rates))
        lower = case / "lower"
        lower.mkdir(exist_ok=True)
        prepared.prepare(
            lower,
            f"read_json {cut}; read_verilog {wrapper}; prep -top TimerIndexOrder -flatten; memory_map; opt; check -assert; write_json {graph}",
            [cut, wrapper],
            [graph],
        )
        callback_cpu.guard_inputs(
            json.loads(graph.read_text()),
            "TimerIndexOrder",
            {
                name: len(port["bits"])
                for name, port in body["ports"].items()
                if port["direction"] == "input"
            },
        )
        for property_name in (
            (
                "orderEquivalent",
                "initialDelayEquivalent",
                "specialReadyEquivalent",
                "rephaseEquivalent",
                "ackEquivalent",
            )
            if prop is None
            else (prop,)
        ):
            query = case / property_name
            query.mkdir(exist_ok=True)
            artifacts.extend(
                callback_cpu.prove(
                    query,
                    f"read_json {graph}",
                    property_name,
                    prop is None,
                    horizon=0,
                    show="-show phase -show owner -show expectedCount -show expectedKind -show domain",
                )
            )
            checks.append(
                {
                    "clock_multiplier": multiplier,
                    "name": name,
                    "property": property_name,
                    "accepted": True,
                }
            )
        artifacts.extend(
            [
                model,
                raw,
                cut,
                wrapper,
                graph,
                case / "prepare.log",
                case / "prepared.json",
                lower / "prepare.log",
                lower / "prepared.json",
            ]
        )
    return checks, artifacts


def native(multiplier, parent, top, observations):
    table = DIRECTORY / "original.tsv"
    selected = fdc_events.fixtures(table.read_text(), NAMES)
    variants = [("positive", table, len(NAMES), "bbc_fdc_branch_test.cpp", observations)]
    artifacts, checks = [], []
    classic_names = ("105-0-0", "83-240-0", "rephased-byte-ack")
    classic = DIRECTORY / "classic.tsv"
    classic.write_text(
        "".join(
            re.sub(r"\Acase [0-9]+ ", f"case {i} ", selected[name])
            for i, name in enumerate(classic_names)
        )
    )
    classic_observations = sum(
        int(selected[name].splitlines()[0].split()[-1]) for name in classic_names
    )
    artifacts.append(classic)
    variants.append(
        ("classic", classic, len(classic_names), "bbc_fdc_epoch_test.cpp", classic_observations)
    )
    for name, (fixture_name, _) in FAULTS.items():
        if multiplier == 3 and name == "post-ack-nmi":
            continue
        fixture = DIRECTORY / (name + ".tsv")
        fixture.write_text(re.sub(r"\Acase [0-9]+ ", "case 0 ", selected[fixture_name]))
        artifacts.append(fixture)
        variants.append((name, fixture, 1, "bbc_fdc_branch_test.cpp", None))
    for name, fixture, count, driver, expected_observations in variants:
        case = DIRECTORY / f"native-{name}{multiplier}"
        case.mkdir(exist_ok=True)
        text = (
            parent.read_text()
            if name in ("positive", "classic")
            else fault(parent.read_text(), name)
        )
        model = case / "actual.v"
        model.write_text(
            re.sub(
                r'\$readmemb\("([^\"]+)"',
                lambda m: '$readmemb("' + str(parent.parent / m[1]) + '"',
                fdc_epoch.native_model(text, top),
            )
        )
        binary = callback_order.compile_native(
            model,
            case,
            "DeviceEpochProbe",
            ROOT / "sim/native" / driver,
            [ROOT / "sim/native/bbc_disc_payload.h", ROOT / "sim/native/bbc_fm_media.h"],
        )
        trial = subprocess.run(
            [
                str(binary),
                str(fixture),
                str(ROOT / "build/bbc/disc.ssd"),
                str(multiplier),
                str(count),
            ],
            capture_output=True,
            text=True,
            timeout=600,
        )
        log = case / "run.log"
        log.write_text(trial.stdout + trial.stderr)
        accepted = (
            (
                trial.returncode == 0
                and not trial.stderr
                and trial.stdout
                == f"FDC epoch: {count} cold cases; {expected_observations} observations; 0 differences\n"
            )
            if name in ("positive", "classic")
            else fdc_events.negative_result(trial, FAULTS[name][0])
        )
        if not accepted:
            raise ValueError("Timer/index public history differs: " + str(log))
        checks.append({"clock_multiplier": multiplier, "name": "native-" + name, "accepted": True})
        artifacts.extend([model, binary, log, case / "build.json", case / "build.log"])
        print(f"Timer/index {multiplier} {name}: PASS", flush=True)
        if name == "positive":
            # Require a mismatch in the final branch, not just the common prefix.
            broken = DIRECTORY / "last-branch.tsv"
            lines = table.read_text().splitlines()
            last = lines[-1].split()
            last[5] = str(1 - int(last[5]))
            lines[-1] = " ".join(last)
            broken.write_text("\n".join(lines) + "\n")
            control = subprocess.run(
                [
                    str(binary),
                    str(broken),
                    str(ROOT / "build/bbc/disc.ssd"),
                    str(multiplier),
                    str(count),
                ],
                capture_output=True,
                text=True,
                timeout=600,
            )
            control_log = case / "last-branch.log"
            control_log.write_text(control.stdout + control.stderr)
            if not fdc_events.negative_result(control, NAMES[-1]):
                raise ValueError("Runner lost final branch mismatch")
            artifacts.extend([broken, control_log])
            checks.append(
                {"clock_multiplier": multiplier, "name": "runner-last-branch", "accepted": True}
            )
    return checks, artifacts


def qualify():
    current_machine.audit()
    DIRECTORY.mkdir(exist_ok=True)
    clocks = [current_machine.DIRECTORY / f"clock{m}/result.json" for m in (1, 3)]
    for clock in clocks:
        memory_composition.sealed(clock)
    reference = source_root("jsbeeb")
    helper = Path(__file__).with_suffix(".mjs")
    files = [
        Path(__file__),
        helper,
        ROOT / "sim/native/bbc_fdc_branch_test.cpp",
        ROOT / "sim/native/bbc_fdc_epoch_test.cpp",
        ROOT / "sim/native/bbc_disc_payload.h",
        ROOT / "sim/native/bbc_fm_media.h",
        ROOT / "tools/bbc/reference_drive.mjs",
        ROOT / "build/bbc/disc.ssd",
        *cpu_events.files(),
        *[ROOT / name for name in current_machine.generation_inputs()],
        current_machine.DIRECTORY / "generation.json",
        *clocks,
        *[parent for _, parent, _ in current_machine.parents()],
        *[
            path
            for _, parent, _ in current_machine.parents()
            for path in parent.parent.glob("*.bin")
        ],
        *sorted((reference / "src").rglob("*.js")),
        *[
            Path(module.__file__)
            for module in (
                current_machine,
                memory_composition,
                bus_binding_proof,
                cpu_events,
                cpu_interrupt_proof,
                callback_cpu,
                callback_order,
                fdc_epoch,
                fdc_events,
                prepared,
            )
        ],
        *[Path(shutil.which(tool)) for tool in ("node", "yosys", "verilator", "c++")],
    ]
    before = hashes(files)
    subprocess.run(
        [
            "node",
            str(helper),
            str(reference),
            str(ROOT / "tools/bbc/reference_drive.mjs"),
            str(ROOT / "build/bbc/disc.ssd"),
            str(DIRECTORY),
        ],
        check=True,
        timeout=120,
    )
    cases, rates = original(DIRECTORY / "original.json")
    selected = fdc_events.fixtures((DIRECTORY / "original.tsv").read_text(), NAMES)
    if any(
        int(selected[case["name"]].splitlines()[0].split()[-1]) != case["rows"] for case in cases
    ):
        raise ValueError("Original row count does not match public fixture")
    checks, artifacts = [], [DIRECTORY / "original.tsv", DIRECTORY / "original.json"]
    for multiplier, parent, top in current_machine.parents():
        for work in (
            lambda: formal(multiplier, parent, top, rates),
            lambda: native(multiplier, parent, top, sum(case["rows"] for case in cases)),
        ):
            c, a = work()
            checks.extend(c)
            artifacts.extend(a)
    current_machine.audit()
    stable = before == hashes(files)
    write_json(
        DIRECTORY / "result.json",
        {
            "passed": stable,
            "inputs_unchanged": stable,
            "inputs": before,
            "artifacts": hashes(artifacts),
            "checks": checks,
            "register_bits": 934,
            "histories": len(NAMES),
            "full_equivalence": False,
            "scope": "Five all-state actual FDC properties cover ready, loaded drive-0 timer/index ordering, source-derived initial seek countdowns, immediate READ SPECIAL completion and its clock temporary, accepted same-drive drive-output rephasing during read payloads, and byte-before-acknowledgement NMI/data behavior. The original executes 36 public ties, 65 register-read histories and one rephased-byte acknowledgement at both clock rates. Three independently reset serial replays cross-check prefix sharing. Seven RTL faults require formal counterexamples; six require named native mismatches at both rates, while the historical post-ack NMI output has an initialized counterexample at 32 MHz. A final-branch fixture fault must propagate. These local domains do not establish complete source timer/serial state correspondence, simultaneous mechanical/byte activity or whole-game equivalence.",
        },
    )
    if not stable:
        raise ValueError("Timer/index qualification inputs changed")


if __name__ == "__main__":
    qualify()
