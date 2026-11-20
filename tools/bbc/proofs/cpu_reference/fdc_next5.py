"""Five local controller correspondences, with executing original projections."""

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

DIRECTORY = ROOT / "build/bbc/fdc-next-five"
KINDS = (
    "mmio-clock",
    "mmio-data",
    "mmio-drive",
    "mmio-read",
    "spec-late",
    "spec-cancel",
    "head",
    "settle",
    "settle-zero",
    "active-settle",
    "parameter",
)
NAMES = [f"{kind}-{offset}" for kind in KINDS for offset in (-1, 0, 1)]
WIDTH = 934
FIELDS = {
    "phase_1": 4,
    "mechanicalPhase": 2,
    "mechanicalDelay": 24,
    "mechanicalOp": 4,
    "dispatchPending": 1,
    "delay": 24,
    "idSector": 8,
    "executingOp": 4,
    "idTrack": 8,
    "mmioClocks": 8,
    "mmioData": 8,
    "driveOut": 8,
    "rotation": 23,
    "headRate": 8,
    "settleRate": 8,
    "stepRate": 8,
    "command": 8,
    "acceptingParameters": 1,
    "specifying": 1,
    "parameterCount": 8,
    "register0": 8,
    "hostParameter": 8,
    "parameters_0": 8,
    "parameters_1": 8,
    "parameters_2": 8,
    "parameters_3": 8,
    "parameters_4": 8,
    "busy": 1,
    "ready": 1,
    "result": 8,
    "nmiLevel": 1,
    "latchedReady": 1,
    "currentTrack0": 8,
    "currentTrack1": 8,
    "physicalTrack": 8,
    "physicalTrack1": 8,
    "headerCrc": 16,
    "headerCrcHigh": 8,
    "badTrack00": 8,
    "badTrack01": 8,
    "badTrack10": 8,
    "badTrack11": 8,
    "mode": 8,
}
PROPERTIES = (
    "mmioEquivalent",
    "specifyEquivalent",
    "timingEquivalent",
    "parametersEquivalent",
    "retryEquivalent",
    "targetEquivalent",
)
EXPORTS = {
    "eventA": "eventAcceptingParameters",
    "eventS": "eventSpecifying",
    "expiry": "indexExpires",
    "indexPulse": "index",
    "atZero": "calibrationAtZero",
    "postDelay": "seekPostDelay",
    "driveIn": "driveIn",
    "headerPresent": "headerPresent",
    "parsing": "parsingHeader",
    "crcClock": "crcClock",
    "target": "retryTarget",
    "nextTarget": "nextRetryTarget",
    "eventBusy": "eventBusy",
    "eventResult": "eventResult",
}


def inventory(body):
    state = body["ports"]["proofState"]["bits"]
    if (
        len(state) != WIDTH
        or len(set(state)) != WIDTH
        or len(body["ports"]["proofNext"]["bits"]) != WIDTH
    ):
        raise ValueError("Incomplete five-boundary state inventory")
    result, used = {}, set()
    for name, width in FIELDS.items():
        bits = body["netnames"][name]["bits"]
        if len(bits) != width or not set(bits).issubset(state) or used.intersection(bits):
            raise ValueError("Invalid five-boundary state: " + name)
        result[name] = [state.index(bit) for bit in bits]
        used.update(bits)
    return result


def assignment_fault(source, name, transform):
    matches = re.findall(r"  assign " + name + r" = ([^;]+);", source)
    if len(matches) != 1:
        raise ValueError("Changed controller boundary: " + name)
    return replace_once(
        source,
        "  assign " + name + " = " + matches[0] + ";",
        "  assign " + name + " = " + transform(matches[0]) + ";",
    )


def fault(source, name):
    if name == "mmio":
        pattern = r"if\(writeSpecialStarts\) begin\n( +)mmioClocks <="
        matches = list(re.finditer(pattern, source))
        if len(matches) != 1:
            raise ValueError("Changed non-cancelling MMIO interface")
        match = matches[0]
        return replace_once(
            source,
            match[0],
            "if(writeSpecialStarts) begin\n"
            + match[1]
            + "if(writeSpecialTarget[5]) mechanicalPhase <= Mechanical_Idle;\n"
            + match[1]
            + "mmioClocks <=",
        )
    if name == "specify":
        return replace_once(
            source,
            "if(specifyStarts) begin",
            "if(specifyStarts) begin\nmechanicalPhase <= Mechanical_Idle;",
        )
    if name == "timing":
        return assignment_fault(source, "seekPostDelay", lambda rhs: "(" + rhs + " >> 1)")
    if name == "parameters":
        matches = list(re.finditer(r"specifying <= 1'b0;", source))
        if len(matches) < 3:
            raise ValueError("Changed SPECIFY clear inventory")
        end = matches[0].end()
        return source[:end] + source[end:].replace(
            "specifying <= 1'b0;", "specifying <= specifying;"
        )
    if name == "stale-acceptance":
        count = 0

        def consumer(m):
            nonlocal count
            lhs, rhs = m[1], m[2]
            if (
                lhs not in ("eventAcceptingParameters", "eventSpecifying")
                and "eventAcceptingParameters" in rhs
            ):
                count += 1
                rhs = rhs.replace("eventAcceptingParameters", "acceptingParameters")
            return "  assign " + lhs + " = " + rhs + ";"

        result = re.sub(r"  assign (\w+) = ([^;]+);", consumer, source)
        if count < 2:
            raise ValueError("Changed post-callback parameter consumers")
        return result
    if name == "retry":
        matches = re.findall(r"  assign \w+ = \(nextRetryCount == 8'h03\);", source)
        if len(matches) != 2:
            raise ValueError("Changed retry termination boundary")
        for match in matches:
            source = replace_once(source, match, match.replace("8'h03", "8'h02"))
        return source
    if name == "target":
        return assignment_fault(
            assignment_fault(source, "retryTarget", lambda rhs: "parameters_0"),
            "nextRetryTarget",
            lambda rhs: "parameters_0",
        )
    raise ValueError("Unknown boundary fault")


def contract(body, enable):
    fields = inventory(body)

    def q(name, vec="proofState"):
        return "{" + ",".join(f"{vec}[{i}]" for i in reversed(fields[name])) + "}"

    inputs, ports, base = [], [], []
    for name, port in body["ports"].items():
        if port["direction"] == "input":
            inputs.append(f"input[{len(port['bits']) - 1}:0] {name}")
            ports.append(f".{name}({name})")
            value = "1'b0" if name in ("io_write", "io_read", "io_warmReset") else name
            base.append(f".{name}({value})")
    decl = []
    exports = []
    for name, signal in EXPORTS.items():
        width = len(body["netnames"][signal]["bits"])
        decl.append(f"wire[{width - 1}:0] {name};")
        exports.append(f".five_{name}({name})")
    mask = sum(1 << i for name in ("hostParameter", "ready") for i in fields[name])
    return f"""module FiveBoundaries({",".join(inputs)},output {",".join(PROPERTIES)});
wire[{WIDTH - 1}:0] nextA,nextB;
{"".join(decl)}
wire callbackNmi,abortEvent,commandEvent;
FdcStateNext actual({",".join(ports)},.proofNext(nextA),.proofEvent(callbackNmi),.proofAbort(abortEvent),.proofCommand(commandEvent),{",".join(exports)});
FdcStateNext baseline({",".join(base)},.proofNext(nextB));
wire enabled={enable};
wire[3:0]phase={q("phase_1")},owner={q("executingOp")};
wire[7:0]cmd={q("command")},drive={q("driveOut")},head={q("headRate")},settle={q("settleRate")},stepRate={q("stepRate")};
wire[23:0]remaining={q("mechanicalDelay")};
wire[1:0]mech={q("mechanicalPhase")};wire[3:0]mechanicalOwner={q("mechanicalOp")};
wire mechanical=(mech==1 || mech==2) && phase==0 && !{q("dispatchPending")};
wire waitDomain=enabled && !reset && !io_warmReset && !expiry && mechanical && remaining!=0;
wire lastParameter=io_write && !io_read && io_address==1 && eventA && {q("parameterCount")}==1 && {q("register0")}==6;
wire[7:0]parameterAddress={q("parameters_0")};
wire mmioDomain=waitDomain && lastParameter && cmd[5:2]==14 && parameterAddress[5];
wire[2:0]slot=parameterAddress[2:0];
wire mmioValue=(slot==3 ? {q("driveOut", "nextA")}==io_data:
 (slot==4 ? fiveDummyClocks==io_data:(slot==5 ? fiveDummyData==io_data:1'b1)));
assign mmioEquivalent=!mmioDomain || ({q("mechanicalPhase", "nextA")}=={q("mechanicalPhase", "nextB")} && {q("mechanicalDelay", "nextA")}=={q("mechanicalDelay", "nextB")} && {q("mechanicalOp", "nextA")}==mechanicalOwner && {q("phase_1", "nextA")}=={q("phase_1", "nextB")} && {q("delay", "nextA")}=={q("delay", "nextB")} &&
 {q("executingOp", "nextA")}=={q("executingOp", "nextB")} && !{q("busy", "nextA")} && !{q("ready", "nextA")} && {q("nmiLevel", "nextA")}=={q("nmiLevel")} && mmioValue);
wire specDomain=waitDomain && io_write && !io_read && io_address==1 && eventA && {q("parameterCount")}==1 && {q("register0")}==7 && cmd[5:2]==13;
assign specifyEquivalent=!specDomain || ({q("mechanicalPhase", "nextA")}=={q("mechanicalPhase", "nextB")} && {q("mechanicalDelay", "nextA")}=={q("mechanicalDelay", "nextB")} && {q("mechanicalOp", "nextA")}==mechanicalOwner && {q("phase_1", "nextA")}=={q("phase_1", "nextB")} && {q("delay", "nextA")}=={q("delay", "nextB")} &&
 {q("executingOp", "nextA")}=={q("executingOp", "nextB")} && {q("specifying", "nextA")} && !{q("acceptingParameters", "nextA")} &&
 {q("parameterCount", "nextA")}==3 && {q("register0", "nextA")}==io_data);
wire finishing=mechanical && mech==1 && remaining==0 && ({q("idSector")}==0 || (({q("idTrack")}==0) && atZero));
wire[23:0]expectedPost=drive[3] ? ({{16'd0,settle}}*24'd64000):({{20'd0,head[3:0]}}*24'd256000);
wire timeDomain=enabled && !reset && !io_warmReset && !io_read && !io_write && !expiry && !indexPulse && finishing && (mechanicalOwner<=7 || mechanicalOwner==10);
wire ready=driveIn[2] && {q("latchedReady")};
wire completes=!ready || mechanicalOwner==10;
assign timingEquivalent=!timeDomain || (postDelay==expectedPost &&
 (postDelay!=0 ? ({q("mechanicalPhase", "nextA")}==2 && {q("mechanicalDelay", "nextA")}==postDelay-1 && {q("phase_1", "nextA")}==0 && callbackNmi=={q("nmiLevel")}):
 ({q("mechanicalPhase", "nextA")}==0 && {q("phase_1", "nextA")}==(completes?0:(mechanicalOwner==6?0:7)) && callbackNmi==({q("nmiLevel")}||completes))));
wire ignoreDomain=enabled && !reset && !io_warmReset && io_write && !io_read && io_address==1 && !eventA && !eventS;
assign parametersEquivalent=!ignoreDomain || (((nextA^nextB)&~{WIDTH}'d{mask})==0 && !{q("ready", "nextA")} && {q("hostParameter", "nextA")}==io_data &&
 ({q("dispatchPending")} || (!{q("acceptingParameters", "nextA")} && !{q("specifying", "nextA")})) && !commandEvent);
wire[7:0]retry={q("parameterCount")},incremented={q("parameterCount")}+8'd1;
wire[7:0]wanted={q("parameters_0")},found={q("idTrack")},oldResult={q("result")};
wire[15:0]crc={q("headerCrc")},supplied={{{q("headerCrcHigh")},io_storageData[7:0]}};
wire[7:0]mode={q("mode")};
wire retryDomain=enabled && !reset && !io_warmReset && !io_read && !io_write && !expiry &&
 phase==7 && mech==0 && !{q("dispatchPending")} && mechanicalOwner==owner && owner<=7 && owner!=6 && cmd[5:2]==owner && mode[5:4]==0 && headerPresent && parsing &&
 {q("rotation")}==crcClock-1 && crc==supplied && found!=wanted && retry<=2;
wire terminal=incremented==3;
wire[7:0]current=nextTarget==0 ? 8'd255:(drive[7]?{q("currentTrack1")}:{q("currentTrack0")});
wire moves=nextTarget!=current && !(nextTarget==0 && atZero);
wire[23:0]retryPost=drive[3]?24'd0:({{20'd0,head[3:0]}}*24'd256000);
wire readyFailure=!moves && retryPost==0 && !ready;
wire retryFinishes=terminal || readyFailure;
assign retryEquivalent=!retryDomain || ({q("parameterCount", "nextA")}==incremented &&
 {q("phase_1", "nextA")}==(retryFinishes?0:((moves || retryPost!=0)?11:7)) &&
 {q("mechanicalPhase", "nextA")}==(terminal?0:(moves?1:(retryPost!=0?2:0))) &&
 {q("executingOp", "nextA")}=={q("executingOp", "nextB")} && callbackNmi==({q("nmiLevel")}||retryFinishes) && !abortEvent &&
 eventResult==(oldResult|(terminal?8'h18:(readyFailure?8'h10:8'd0))) && eventBusy==({q("busy")}&&!retryFinishes));
wire[8:0]sum={{1'b0,wanted}}+{{1'd0,retry}};
wire[7:0]bad0=drive[7]?{q("badTrack10")}:{q("badTrack00")};
wire[7:0]bad1=drive[7]?{q("badTrack11")}:{q("badTrack01")};
wire skip0=sum!=0 && {{1'b0,bad0}}<=sum;
wire[7:0]first=sum+skip0;
wire skip1=sum!=0 && bad1<=first;
wire[7:0]expectedTarget=sum+skip0+skip1;
assign targetEquivalent=target==expectedTarget;
wire[7:0]fiveDummyClocks={q("mmioClocks", "nextA")},fiveDummyData={q("mmioData", "nextA")};
endmodule
"""


def formal(multiplier, parent, top):
    controller, extra = callback_order.fdc_instance(parent.read_text(), top)
    controller = re.sub(
        r"(  reg +eventAcceptingParameters;)", r"  (* keep *) \1", controller, count=1
    )
    artifacts, checks = [], []
    variants = [("positive", controller, None)]
    for name, prop in zip(
        ("mmio", "specify", "timing", "parameters", "retry", "target"), PROPERTIES
    ):
        variants.append((name, fault(controller, name), prop))
    variants.append(
        ("stale-acceptance", fault(controller, "stale-acceptance"), "parametersEquivalent")
    )
    for name, source, negative in variants:
        case = DIRECTORY / f"formal-{name}{multiplier}"
        case.mkdir(exist_ok=True)
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
        for key, signal in EXPORTS.items():
            body["ports"]["five_" + key] = {
                "direction": "output",
                "bits": body["netnames"][signal]["bits"],
            }
        write_json(cut, data)
        wrapper.write_text(contract(body, extra[0] if extra else "1'b1"))
        lower = case / "lower"
        lower.mkdir(exist_ok=True)
        prepared.prepare(
            lower,
            f"read_json {cut}; read_verilog {wrapper}; prep -top FiveBoundaries -flatten; opt; check -assert; write_json {graph}",
            [cut, wrapper],
            [graph],
        )
        callback_cpu.guard_inputs(
            json.loads(graph.read_text()),
            "FiveBoundaries",
            {n: len(p["bits"]) for n, p in body["ports"].items() if p["direction"] == "input"},
        )
        for prop in PROPERTIES if name == "positive" else (negative,):
            query = case / prop
            query.mkdir(exist_ok=True)
            slim = query / "graph.json"
            others = " ".join("FiveBoundaries/w:" + p for p in PROPERTIES if p != prop)
            prepared.prepare(
                query,
                f"read_json {graph}; delete -output {others}; opt_clean -purge; check -assert; write_json {slim}",
                [graph],
                [slim],
            )
            callback_cpu.guard_inputs(
                json.loads(slim.read_text()),
                "FiveBoundaries",
                {n: len(p["bits"]) for n, p in body["ports"].items() if p["direction"] == "input"},
            )
            logs = callback_cpu.prove(
                query,
                f"read_json {slim}; opt -full; opt_clean -purge",
                prop,
                name == "positive",
                horizon=0,
                show="-show-inputs",
            )
            checks.append(
                {"clock_multiplier": multiplier, "name": name, "property": prop, "accepted": True}
            )
            artifacts.extend(logs)
            artifacts.extend([slim, query / "prepared.json", query / "prepare.log"])
        artifacts.extend(
            [
                model,
                raw,
                cut,
                wrapper,
                graph,
                case / "prepared.json",
                case / "prepare.log",
                lower / "prepared.json",
                lower / "prepare.log",
            ]
        )
    return checks, artifacts


def source_projection(report):
    budget = report.get("budget")
    expected = [["finish", 24, 3] if i == 2 else ["retry", 1, (i + 1) & 255] for i in range(256)]
    if (
        report.get("passed") is not True
        or report.get("target_cases") != 100663296
        or report.get("head_cases") != 262144
        or budget != expected
        or report.get("step_ms") != [3 if i == 0 else i * 2 for i in range(256)]
    ):
        raise ValueError("Incomplete original retry/head/rate projection")
    # These rows come from executing _byteCallbackReading, including the
    # terminal increment and byte wrap. R1 now retains exactly the source byte.
    rows = "\n".join(f"8'd{i}:sourceNext=8'd{budget[i][2]};" for i in range(256))
    return f"function[7:0]sourceNext(input[7:0]count);begin case(count)\n{rows}\nendcase end endfunction"


def budget_contract(projection):
    return f"""module RetryBudget(input clk,start,header,crcGood,mismatch,finish,program,input[7:0]programData,output equivalent,observation);
{projection}
reg[7:0]sourceCount=0;
reg[7:0]actualCount=0;
reg sourceActive=0,actualActive=0;
wire retryS=sourceActive && header && crcGood && mismatch && sourceCount!=2;
wire retryA=actualActive && header && crcGood && mismatch && actualCount!=2;
wire terminalS=sourceActive && header && crcGood && mismatch && sourceCount==2;
wire terminalA=actualActive && header && crcGood && mismatch && actualCount==2;
wire crcS=sourceActive && header && !crcGood,crcA=actualActive && header && !crcGood;
assign observation=retryS==retryA && terminalS==terminalA && crcS==crcA;
assign equivalent=observation && sourceActive==actualActive && sourceCount==actualCount;
always @(posedge clk) begin
 if(sourceActive && header && crcGood && mismatch)sourceCount<=sourceNext(sourceCount);
 if(actualActive && header && crcGood && mismatch)actualCount<=actualCount+1'b1;
 if(terminalS || crcS || finish)sourceActive<=0;
 if(terminalA || crcA || finish)actualActive<=0;
 if(start)begin sourceCount<=0;actualCount<=0;sourceActive<=1;actualActive<=1;end
 if(program)begin sourceCount<=programData;actualCount<=programData;end
end
endmodule
"""


def budget(projection):
    checks, artifacts = [], []
    text = budget_contract(projection)
    for name, body in (
        ("positive", text),
        (
            "early-stop",
            replace_once(text, "actualCount!=2", "actualCount!=1").replace(
                "actualCount==2;", "actualCount==1;"
            ),
        ),
    ):
        case = DIRECTORY / ("budget-" + name)
        case.mkdir(exist_ok=True)
        model, graph = case / "contract.v", case / "graph.json"
        model.write_text(body)
        prepared.prepare(
            case,
            f"read_verilog -formal {model}; prep -top RetryBudget -flatten; memory_map; opt; check -assert; write_json {graph}",
            [model],
            [graph],
        )
        callback_cpu.guard_inputs(
            json.loads(graph.read_text()),
            "RetryBudget",
            {
                **dict.fromkeys(
                    ("clk", "start", "header", "crcGood", "mismatch", "finish", "program"), 1
                ),
                "programData": 8,
            },
            sequential=True,
        )
        logs = callback_cpu.prove(
            case,
            f"read_json {graph}",
            "equivalent" if name == "positive" else "observation",
            name == "positive",
            induction=name == "positive",
            horizon=8,
            show="-show-inputs -show sourceCount -show actualCount -show sourceActive -show actualActive",
        )
        artifacts.extend([model, graph, case / "prepared.json", case / "prepare.log", *logs])
        checks.append({"name": "budget-" + name, "accepted": True})
    return checks, artifacts


def negative_result(trial, name):
    expected = {
        "mmio": "mmio-clock--1",
        "specify": "spec-late--1",
        "timing": "head--1",
        "parameters": "spec-late-1",
        "retry": "track-retry",
        "target": "track-retry",
    }
    return fdc_events.negative_result(trial, expected[name])


def native(multiplier, parent, top, table, retry_table, observations, retry_observations):
    checks, artifacts = [], []
    selected = fdc_events.fixtures(table.read_text(), NAMES)
    variants = [
        ("positive", table, 33, observations),
        ("positive-retry", retry_table, 1, retry_observations),
    ]
    for name, case_name in (
        ("mmio", "mmio-clock--1"),
        ("specify", "spec-late--1"),
        ("timing", "head--1"),
        ("parameters", "spec-late-1"),
        ("retry", "track-retry"),
        ("target", "track-retry"),
    ):
        fixture = DIRECTORY / (name + "-fault.tsv")
        rows = retry_table.read_text() if case_name == "track-retry" else selected[case_name]
        # The native reader requires a fresh zero-based case inventory.
        fixture.write_text(re.sub(r"\Acase [0-9]+ ", "case 0 ", rows))
        artifacts.append(fixture)
        variants.append((name, fixture, 1, None))
    for name, fixture, count, rows in variants:
        case = DIRECTORY / f"native-{name}{multiplier}"
        case.mkdir(exist_ok=True)
        source = parent.read_text()
        if not name.startswith("positive"):
            source = fault(source, name)
        model = case / "actual.v"
        model.write_text(
            re.sub(
                r'\$readmemb\("([^\"]+)"',
                lambda m: '$readmemb("' + str(parent.parent / m[1]) + '"',
                fdc_epoch.native_model(source, top),
            )
        )
        binary = callback_order.compile_native(
            model,
            case,
            "DeviceEpochProbe",
            ROOT / "sim/native/bbc_fdc_branch_test.cpp",
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
                == f"FDC epoch: {count} cold cases; {rows} observations; 0 differences\n"
            )
            if name.startswith("positive")
            else negative_result(trial, name)
        )
        checks.append(
            {"clock_multiplier": multiplier, "name": "native-" + name, "accepted": accepted}
        )
        artifacts.extend([model, binary, log, case / "build.json", case / "build.log"])
        print(
            f"Five boundaries {multiplier} {name}: " + ("PASS" if accepted else "FAIL"), flush=True
        )
        if not accepted:
            raise ValueError("Five-boundary public history differs: " + str(log))
    return checks, artifacts


def qualify():
    current_machine.audit()
    DIRECTORY.mkdir(parents=True, exist_ok=True)
    clocks = [current_machine.DIRECTORY / f"clock{m}/result.json" for m in (1, 3)]
    for path in clocks:
        memory_composition.sealed(path)
    reference = source_root("jsbeeb")
    helper = Path(__file__).with_suffix(".mjs")
    retry_helper = Path(__file__).with_name("fdc_retry_source.mjs")
    files = [
        Path(__file__),
        helper,
        retry_helper,
        Path(fdc_events.__file__).with_suffix(".mjs"),
        ROOT / "sim/native/bbc_fdc_branch_test.cpp",
        ROOT / "sim/native/bbc_disc_payload.h",
        ROOT / "sim/native/bbc_fm_media.h",
        ROOT / "tools/bbc/reference_drive.mjs",
        ROOT / "build/bbc/disc.ssd",
        *cpu_events.files(),
        *[ROOT / n for n in current_machine.generation_inputs()],
        current_machine.DIRECTORY / "generation.json",
        *clocks,
        *[p for _, p, _ in current_machine.parents()],
        *[b for _, p, _ in current_machine.parents() for b in p.parent.glob("*.bin")],
        *sorted((reference / "src").rglob("*.js")),
        *[
            Path(m.__file__)
            for m in (
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
        ROOT / "tools/bbc/proofs/video_cursor.py",
        ROOT / "tools/validation/common.py",
        *[Path(shutil.which(t)) for t in ("node", "yosys", "verilator", "c++")],
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
        timeout=60,
    )
    subprocess.run(
        ["node", str(retry_helper), str(reference), str(DIRECTORY)], check=True, timeout=120
    )
    original = json.loads((DIRECTORY / "original.json").read_text())
    if original.get("passed") is not True or [c["name"] for c in original["cases"]] != NAMES:
        raise ValueError("Incomplete five-boundary original histories")
    table = DIRECTORY / "original.tsv"
    fdc_events.fixtures(table.read_text(), NAMES)
    observations = sum(c["rows"] for c in original["cases"])
    retry_public = DIRECTORY / "retry-public"
    retry_public.mkdir(exist_ok=True)
    subprocess.run(
        [
            "node",
            str(Path(fdc_events.__file__).with_suffix(".mjs")),
            str(reference),
            str(ROOT / "tools/bbc/reference_drive.mjs"),
            str(ROOT / "build/bbc/disc.ssd"),
            str(retry_public),
        ],
        check=True,
        timeout=60,
    )
    retry_fixture = fdc_events.fixtures((retry_public / "original.tsv").read_text())["track-retry"]
    retry_table = DIRECTORY / "track-retry.tsv"
    retry_table.write_text(re.sub(r"\Acase [0-9]+ ", "case 0 ", retry_fixture))
    retry_observations = len(retry_fixture.splitlines()) - 1
    artifacts = [
        table,
        DIRECTORY / "original.json",
        DIRECTORY / "retry-source.json",
        DIRECTORY / "seek-prefix.js",
        retry_public / "original.json",
        retry_public / "original.tsv",
        retry_table,
    ]
    projection = source_projection(json.loads((DIRECTORY / "retry-source.json").read_text()))
    checks, a = budget(projection)
    artifacts.extend(a)
    for multiplier, parent, top in current_machine.parents():
        c, a = formal(multiplier, parent, top)
        checks.extend(c)
        artifacts.extend(a)
        c, a = native(multiplier, parent, top, table, retry_table, observations, retry_observations)
        checks.extend(c)
        artifacts.extend(a)
    current_machine.audit()
    for path in clocks:
        memory_composition.sealed(path)
    stable = before == hashes(files)
    write_json(
        DIRECTORY / "result.json",
        {
            "passed": stable,
            "inputs_unchanged": stable,
            "inputs": before,
            "artifacts": hashes(artifacts),
            "checks": checks,
            "register_bits": WIDTH,
            "source_cases": 34,
            "observations_per_clock": observations + retry_observations,
            "source_target_cases": 100663296,
            "source_head_cases": 262144,
            "scope": "Six all-state actual 934-bit FDC transition properties at each master rate cover non-cancelling MMIO writes, concurrent SPECIFY setup, live final-step head/settle allocation, post-callback rejection of parameter writes, good-ID-CRC mismatch retry decisions and byte-wrapped bad-track target arithmetic. Each property has a solver-checked behavioral fault, with an additional stale-acceptance fault. The maintained original executes the seek arithmetic prefix verbatim for all 100663296 normal-epoch input combinations, all 256 retry-counter decisions and 262144 head/settle allocations. Initialized unbounded byte-budget induction retains the exact terminal increment and allows arbitrary counter programming during active and inactive epochs. Thirty-four cold public histories per clock include before/on/after overlaps, later READ rotation timing, active-delay retention, live DIR pulses and two retries. Every physical CPU detector sample is counted; six native faults must yield named observation mismatches.",
            "limits": "Local RTL transition domains plus finite executing-source projections, not a universal JavaScript/controller-state translation. MMIO/SPECIFY setup queries cover waiting Seek/Delay and valid final parameter positions; finishing and index-timeout collisions are separate. Ignored-parameter commutation covers every callback-cleared acceptance state. Head timing queries cover final steps, with no simultaneous host/index callback. The phase/result query retains its normal 0..2 retry domain; the target constructor and byte-budget relation cover all R1 values. The separate fdc-shared certificate covers canonical R0/R1, seek/ID and index aliases plus interference histories. Full callback-state correspondence remains open. Public histories use the declared SSD/clocked-FM drive and actual parent bus with held requests in place of CPU instructions. Arbitrary media, external CPU warm resets, whole-game and physical-board equivalence remain open.",
            "universal_game_equivalence": False,
        },
    )
    if not stable:
        raise ValueError("Five-boundary proof inputs changed")


if __name__ == "__main__":
    qualify()
