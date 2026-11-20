"""Qualify original ID CRC decisions, IRQ prefixes and retained read/write context."""

import json
import re
import shutil
import subprocess
from pathlib import Path

from tools.bbc.proofs import current_machine, memory_composition
from tools.bbc.proofs.cpu_reference import (
    callback_cpu,
    callback_order,
    fdc_bits,
    fdc_byte,
    fdc_concurrent,
    fdc_epoch,
    fdc_events,
    prepared,
)
from tools.bbc.proofs.video_cursor import hashes, replace_once
from tools.validation.common import ROOT, source_root, write_json

DIRECTORY = ROOT / "build/bbc/fdc-source-crc"
WIDTH = 934
FIELDS = {
    "phase_1": 4,
    "mechanicalPhase": 2,
    "mechanicalOp": 4,
    "mechanicalDelay": 24,
    "dispatchPending": 1,
    "executingOp": 4,
    "command": 8,
    "mode": 8,
    "parameterCount": 8,
    "parameters_0": 8,
    "parameters_1": 8,
    "parameters_2": 8,
    "headerCrc": 16,
    "headerCrcHigh": 8,
    "rotation": 23,
    "headerSeen": 1,
    "headerRecovering": 1,
    "delay": 24,
    "driveOut": 8,
    "physicalTrack": 8,
    "currentTrack0": 8,
    "latchedReady": 1,
    "result": 8,
    "busy": 1,
    "ready": 1,
    "request": 1,
    "interrupt": 1,
    "nmiLevel": 1,
    "data": 8,
    "idTrack": 8,
    "idSector": 8,
    "readDataPending": 1,
    "driveInCopy": 8,
    "byteCounterLow": 8,
    "byteCounterHigh": 8,
    "headerPointer": 8,
}
EXPORTS = {
    "indexPulse": ("index", 1),
    "present": ("headerPresent", 1),
    "cell": ("cellBoundary", 1),
    "crcClock": ("crcClock", 23),
    "target": ("nextRetryTarget", 8),
    "driveIn": ("driveIn", 8),
    "step": ("stepDelay", 24),
    "aborted": ("serialAbort", 1),
    "headerWord": ("headerWord", 3),
    "headerIrqWord": ("headerIrqWord", 1),
    "specialValue": ("liveSpecial", 8),
    "specialValid": ("liveSpecialValid", 1),
}
PROPERTIES = (
    "errorEquivalent",
    "completionEquivalent",
    "retryEquivalent",
    "skipPhaseEquivalent",
    "waitEquivalent",
    "tailEquivalent",
    "prefixEquivalent",
    "routeCovered",
    "statusEquivalent",
)
FAULTS = {
    "raw-command": ("search-id-command-216--1", "retryEquivalent"),
    "call-context": ("retained-write-context", "skipPhaseEquivalent"),
    "stopped-tail": ("id-retry-irq-129", "tailEquivalent"),
    "short-tail": ("id-retry-irq-129", "tailEquivalent"),
    "header-irqs": ("crc-irq-177-0--1", "prefixEquivalent"),
    "header-stop": ("header-stop-144", "prefixEquivalent"),
    "command-full": ("header-stop-64", "statusEquivalent"),
}


def fault(source, name):
    if name == "command-full":
        target = "mode[6] <= 1'b0;"
        if source.count(target) != 1:
            raise ValueError("Changed status register projection")
        return source.replace(target, "mode[6] <= mode[6];")
    if name in ("header-irqs", "header-stop"):
        pattern = (
            r"(?m)^  assign (headerIrqWord) = ([^;]+);$"
            if name == "header-irqs"
            else r"(?m)^  assign (\w+) = ([^;]*headerIrqWord[^;]*searchHeaderWord[^;]*headerWord == 3'b100[^;]*headerIrqLoss[^;]*);$"
        )
        found = re.findall(pattern, source)
        if len(found) != 1:
            raise ValueError("Changed header IRQ/stop boundary")
        signal, rhs = found[0]
        return source.replace(
            "  assign " + signal + " = " + rhs + ";", "  assign " + signal + " = 1'b0;"
        )
    if name in ("raw-command", "call-context"):
        target = "idCrcIsReadId" if name == "raw-command" else "idHeaderWriteContext"
        m = re.findall(r"(?m)^  assign " + target + r" = ([^;]+);$", source)
        if len(m) != 1:
            raise ValueError("Changed CRC predicate: " + target)
        rhs = (
            "(command[5:2] == 4'd6)"
            if name == "raw-command"
            else "((preByteOwner == 4'd2) || (preByteOwner == 4'd3))"
        )
        return source.replace(
            "  assign " + target + " = " + m[0] + ";", "  assign " + target + " = " + rhs + ";"
        )
    matches = re.findall(
        r"(?m)^  assign (\w+) = ([^;]*Phase_IdCrcWait[^;]*delay == 24'h0[^;]*);$", source
    )
    if len(matches) != 1:
        raise ValueError("Changed CRC continuation callback")
    signal, rhs = matches[0]
    if name == "stopped-tail":
        return source.replace(
            "  assign " + signal + " = " + rhs + ";", "  assign " + signal + " = 1'b0;"
        )
    if name != "short-tail":
        raise ValueError("Unknown CRC fault")
    pattern = r"(if\s*\(" + signal + r"\) begin\n\s+delay <= )\(24'h000800 - 24'h000001\);"
    changed, n = re.subn(pattern, r"\g<1>(24'h000400 - 24'h000001);", source)
    if n != 1:
        raise ValueError("Changed CRC continuation allocation: " + str(n))
    return changed


NAMES = (
    [f"id-live-{m}" for m in (128, 129, 144, 145, 160, 161, 176, 177)]
    + [f"search-id-command-{c}-{o}" for c in (24, 216) for o in (-1, 0, 1)]
    + [f"id-retry-irq-{m}" for m in (129, 177)]
    + [f"id-retries-{m}" for m in (129, 177)]
    + ["retained-write-context"]
    + [
        f"crc-irq-{m}-{a}-{o}"
        for m in (128, 129, 144, 145, 160, 161, 176, 177)
        for a in (0, 1)
        for o in (-1, 0, 1)
    ]
    + [f"header-irq-{m}-{a}" for m in (128, 129, 144, 145, 160, 161, 176, 177) for a in (0, 1)]
    + [f"header-stop-{m}" for m in (64, 65, 144, 145, 160, 161)]
)
SUMMARY = "Original ID CRC: 1189888 body, 1024 default stop, 256 status cases; 0 differences\n"


def indices(body):
    state = body["ports"]["proofState"]["bits"]
    if (
        len(state) != WIDTH
        or len(set(state)) != WIDTH
        or len(body["ports"]["proofNext"]["bits"]) != WIDTH
    ):
        raise ValueError("Incomplete ID CRC controller state")
    fields, used = {}, set()
    for n, w in FIELDS.items():
        b = body["netnames"][n]["bits"]
        if len(b) != w or not set(b).issubset(state) or used.intersection(b):
            raise ValueError("Changed ID CRC field: " + n)
        fields[n] = [state.index(v) for v in b]
        used.update(b)
    return fields


def contract(body, enable):
    fields = indices(body)
    W = WIDTH

    def q(n, v="proofState"):
        return "{" + ",".join(f"{v}[{i}]" for i in reversed(fields[n])) + "}"

    inputs = []
    ports = []
    for n, p in body["ports"].items():
        if p["direction"] == "input":
            inputs.append(f"input[{len(p['bits']) - 1}:0]{n}")
            ports.append(f".{n}({n})")
    ports.extend(f".probe_{n}({n})" for n in EXPORTS)
    mapped = {
        1: "parameterCount",
        6: "parameters_1",
        7: "parameters_0",
        10: "idSector",
        12: "idTrack",
        19: "byteCounterLow",
        20: "byteCounterHigh",
        21: "driveInCopy",
        31: "command",
    }
    assignments = "".join(
        f"assign regs[{8 * a}+:8]={q(n)};\n" for a, n in mapped.items()
    ) + "".join(f"assign regs[{8 * a}+:8]=8'd0;\n" for a in range(32) if a not in mapped)
    frameenable = enable
    return f"""module IdCrcBinding({",".join(inputs)},output {",".join(PROPERTIES)});
wire[{W - 1}:0]following;wire indexPulse,present,cell,aborted,headerIrqWord,specialValid;wire[7:0]specialValue;wire[2:0]headerWord;wire[22:0]crcClock;wire[7:0]target,driveIn;wire[23:0]step;
FdcStateNext actual({",".join(ports)},.proofNext(following));
wire[255:0]regs,nextRegs;wire[53:0]nextCount;wire[31:0]nextCrc;wire finished,completed,seeking,stateWritten;wire[7:0]finishResult,seekContext,nextState,effectCount;
{assignments}
wire[3:0]owner={q("executingOp")};wire[7:0]mode={q("mode")},drive={q("driveOut")};
wire[3:0]contextOp={q("mechanicalOp")};wire writeContext=contextOp==2 || contextOp==3;
FdcIdCrcSource source(.registers(regs),.stateCount(54'd1),.onDiscCrc({{24'd0,{q("headerCrcHigh")}}}),.crc({q("headerCrc")}),.byteValue(io_storageData[7:0]),.callContext(writeContext?8'd5:(contextOp==6?8'd3:(contextOp==10?8'd2:8'd4))),.followingRegisters(nextRegs),.followingCount(nextCount),.followingCrc(nextCrc),.finished(finished),.completed(completed),.seeking(seeking),.stateWritten(stateWritten),.finishResult(finishResult),.seekContext(seekContext),.nextState(nextState),.effectCount(effectCount));
wire base={frameenable} && !reset && !io_warmReset && !io_read && !io_write && !indexPulse && !{q("dispatchPending")} && ({q("mechanicalPhase")}==0 || {q("mechanicalDelay")}>0) && cell && present && io_storageData[15:8]==255 && !drive[0] && {q("rotation")}<6400000;
wire frame=base && (!irqs || sourceOk) && {q("mechanicalPhase")}==0 && owner>=2 && owner<=7 && (contextOp<=7 || contextOp==10) && {q("rotation")}==crcClock-23'd1 &&
 (({q("phase_1")}==7 && {q("headerSeen")} && !{q("headerRecovering")}) || ({q("phase_1")}==9 && owner==6 && {q("delay")}==0));
assign errorEquivalent=!frame || !finished || ({q("phase_1", "following")}==0 && {q("mechanicalPhase", "following")}==0 && !{q("busy", "following")} && {q("ready", "following")} && {q("nmiLevel", "following")} && {q("result", "following")}==({q("result")}|finishResult) && {q("parameterCount", "following")}==nextRegs[8+:8]);
wire completes=frame && completed && {q("parameters_2")}[4:0]==1 && driveIn[2] && {q("latchedReady")};
assign completionEquivalent=!completes || ({q("phase_1", "following")}==0 && {q("mechanicalPhase", "following")}==0 && !{q("busy", "following")} && {q("ready", "following")} && {q("nmiLevel", "following")} && {q("result", "following")}=={q("result")} && {q("parameters_2", "following")}=={q("parameters_2")}-8'd1);
wire retry=frame && seeking && target!=0 && target!={q("currentTrack0")} && {q("physicalTrack")}<79 && drive[3] && step>0;
assign retryEquivalent=!retry || (seekContext==1 && {q("parameterCount", "following")}==nextRegs[8+:8] && {q("phase_1", "following")}==11 && {q("mechanicalPhase", "following")}==1 && {q("mechanicalDelay", "following")}==step-24'd1 && {q("delay", "following")}==2047 && {q("nmiLevel", "following")}==({q("nmiLevel")}||irqs));
assign skipPhaseEquivalent=!frame || !stateWritten || nextState!=11 || ({q("phase_1", "following")}==8 && {q("executingOp", "following")}==contextOp && {q("driveInCopy", "following")}==nextRegs[168+:8] && ({q("byteCounterLow", "following")}==nextRegs[152+:8] && {q("byteCounterHigh", "following")}==nextRegs[160+:8]) && {q("delay", "following")}==(writeContext?24'd34815:24'd38911));
assign waitEquivalent=!frame || !stateWritten || nextState!=2 || ({q("phase_1", "following")}==7 && {q("headerRecovering", "following")} && !{q("headerSeen", "following")} && {q("delay", "following")}==8191);
wire tail=base && {q("phase_1")}==11 && {q("delay")}==0;
wire irqs,sourceOk,sourceAbort,sourceFinish;wire[7:0]sourceResult;
wire[7:0]logicalStatus=(mode & 8'h73) | ({q("request")}?8'd4:8'd0) | ({q("interrupt")}?8'd8:8'd0) | ({q("busy")}?8'd128:8'd0);
FdcIrqSource irqSource(.low(8'd0),.high(8'd0),.status(logicalStatus),.command({q("command")}),.value(irqs));
FdcLossSource lossSource(.low(8'd0),.high(8'd0),.status(logicalStatus),.command({q("command")}),.value(sourceOk),.aborted(sourceAbort),.finished(sourceFinish),.finishResult(sourceResult));
wire bad=irqs && !sourceOk;
assign tailEquivalent=!tail || (aborted==bad && (bad?(sourceAbort && sourceFinish && sourceResult==8'h0a && {q("phase_1", "following")}==0 && {q("mechanicalPhase", "following")}==0 && {q("nmiLevel", "following")} && {q("result", "following")}==({q("result")}|8'h0a)):
 ({q("phase_1", "following")}==11 && {q("delay", "following")}==2047 && (irqs?({q("data", "following")}==io_storageData[7:0] && {q("nmiLevel", "following")} && {q("request", "following")} && {q("interrupt", "following")}):({q("data", "following")}=={q("data")} && {q("nmiLevel", "following")}=={q("nmiLevel")} && {q("request", "following")}=={q("request")} && {q("interrupt", "following")}=={q("interrupt")})))));
wire[7:0]stopStatus,nextPointer;wire stopping;
FdcHeaderStopSource stopSource(.status(logicalStatus),.pointer({q("headerPointer")}),.followingStatus(stopStatus),.followingPointer(nextPointer),.stopping(stopping));
wire prefix=base && headerWord>0 && headerWord<=6 && (({q("phase_1")}==7 && {q("headerSeen")} && !{q("headerRecovering")}) || ({q("phase_1")}==9 && owner==6 && headerWord>=5)) && (headerWord>=5 || {q("headerPointer")}==8'd13-{{5'd0,headerWord}});
wire[7:0]followingMode={q("mode", "following")};
assign prefixEquivalent=!prefix || (headerIrqWord && aborted==bad && (bad?
 (sourceAbort && sourceFinish && sourceResult==8'h0a && {q("phase_1", "following")}==0 && {q("mechanicalPhase", "following")}==0 && !{q("busy", "following")} && {q("ready", "following")} && {q("nmiLevel", "following")} && {q("data", "following")}=={q("data")} && {q("result", "following")}==({q("result")}|sourceResult) && {q("headerCrc", "following")}=={q("headerCrc")} && {q("headerCrcHigh", "following")}=={q("headerCrcHigh")} && {q("parameterCount", "following")}=={q("parameterCount")}) :
 ((irqs?({q("data", "following")}==io_storageData[7:0] && {q("nmiLevel", "following")} && {q("interrupt", "following")} && {q("request", "following")}):({q("data", "following")}=={q("data")} && {q("request", "following")}=={q("request")})) && (headerWord>4 || followingMode[5:4]==stopStatus[5:4]))));
assign routeCovered=!frame || (effectCount==1 && (finished || completed || seeking || stateWritten) && (!stateWritten || nextState==2 || nextState==11));
wire[7:0]sourceStatusValue;wire[7:0]parameter0={q("parameters_0")};
FdcStatusReadSource statusSource(.status(logicalStatus),.value(sourceStatusValue));
wire[7:0]commandStatus;
FdcCommandStatusSource commandSource(.status(logicalStatus),.value(commandStatus));
assign statusEquivalent=(parameter0[5:0]!=23 || (specialValid && specialValue==sourceStatusValue)) &&
 (!({frameenable} && !reset && !io_warmReset && io_write && io_address==0 && !{q("dispatchPending")} &&
     (io_data[5:2]!=14 || io_data[1:0]!=0)) || followingMode[6]==commandStatus[6]);
endmodule
""".replace(q("parameters_2") + "[4:0]", "(" + q("parameters_2") + " & 8'd31)")


def formal(multiplier, parent, top):
    controller, extra = callback_order.fdc_instance(parent.read_text(), top)
    checks = []
    artifacts = []
    for name in ("positive", *FAULTS):
        case = DIRECTORY / f"formal-{name}{multiplier}"
        case.mkdir(exist_ok=True)
        text = controller if name == "positive" else fault(controller, name)
        text = replace_once(
            text,
            "module Intel8271 (",
            "module Intel8271 ("
            + ",".join(f"output[{w - 1}:0]probe_{n}" for n, (_, w) in EXPORTS.items())
            + ",",
        )
        text = replace_once(
            text,
            "endmodule",
            "\n"
            + "".join(f"assign probe_{n}={signal};\n" for n, (signal, _) in EXPORTS.items())
            + "endmodule",
        )
        model, raw, cut, wrapper, graph = [
            case / n for n in ("actual.v", "raw.json", "cut.json", "contract.v", "graph.json")
        ]
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
        write_json(cut, data)
        wrapper.write_text(contract(body, extra[0] if extra else "1'b1"))
        lower = case / "lower"
        lower.mkdir(exist_ok=True)
        prepared.prepare(
            lower,
            f"read_json {cut}; read_verilog {DIRECTORY / 'source.v'} {wrapper}; prep -top IdCrcBinding -flatten; opt; check -assert; write_json {graph}",
            [cut, DIRECTORY / "source.v", wrapper],
            [graph],
        )
        for prop in PROPERTIES if name == "positive" else (FAULTS[name][1],):
            query = case / prop
            query.mkdir(exist_ok=True)
            slim = query / "graph.json"
            others = " ".join("IdCrcBinding/w:" + p for p in PROPERTIES if p != prop)
            prepared.prepare(
                query,
                f"read_json {graph}; delete -output {others}; opt_clean -purge; check -assert; write_json {slim}",
                [graph],
                [slim],
            )
            callback_cpu.guard_inputs(
                json.loads(slim.read_text()),
                "IdCrcBinding",
                {n: len(p["bits"]) for n, p in body["ports"].items() if p["direction"] == "input"},
            )
            artifacts.extend(
                callback_cpu.prove(
                    query,
                    f"read_json {slim}; opt_clean -purge",
                    prop,
                    name == "positive",
                    horizon=0,
                    show="-show-inputs",
                )
            )
            checks.append(
                {"name": name, "property": prop, "clock_multiplier": multiplier, "accepted": True}
            )
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


def native(multiplier, parent, top, observations):
    table = DIRECTORY / "original.tsv"
    selected = fdc_events.fixtures(table.read_text(), NAMES)
    checks, artifacts = [], []
    for name in ("positive", *FAULTS):
        case = DIRECTORY / f"native-{name}{multiplier}"
        case.mkdir(exist_ok=True)
        fixture = table
        count = len(NAMES)
        media = DIRECTORY
        if name != "positive":
            witness = FAULTS[name][0]
            i = NAMES.index(witness)
            fixture = case / "original.tsv"
            fixture.write_text(re.sub(r"\Acase [0-9]+ ", "case 0 ", selected[witness]))
            count = 1
            media = case
            shutil.copyfile(DIRECTORY / f"media-{i}.bin", case / "media-0.bin")
            artifacts.extend([fixture, case / "media-0.bin"])
        text = parent.read_text() if name == "positive" else fault(parent.read_text(), name)
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
                str(media),
            ],
            capture_output=True,
            text=True,
            timeout=900,
        )
        log = case / "run.log"
        log.write_text(trial.stdout + trial.stderr)
        accepted = (
            (
                trial.returncode == 0
                and not trial.stderr
                and trial.stdout
                == f"FDC epoch: {count} cold cases; {observations} observations; 0 differences\n"
            )
            if name == "positive"
            else (
                fdc_events.negative_result(trial, FAULTS[name][0])
                or fdc_concurrent.media_mismatch(trial, FAULTS[name][0])
            )
        )
        if not accepted:
            raise ValueError("ID CRC source history differs: " + str(log))
        artifacts.extend([model, binary, log, case / "build.json", case / "build.log"])
        checks.append({"name": "native-" + name, "clock_multiplier": multiplier, "accepted": True})
        print("ID CRC", multiplier, name, "PASS", flush=True)
        if name == "positive":
            broken = case / "last-media"
            broken.mkdir(exist_ok=True)
            fdc_concurrent.stage_fixture_fault(
                DIRECTORY, broken, (f"media-{i}.bin" for i in range(count)),
                f"media-{count - 1}.bin",
            )
            control = subprocess.run(
                [
                    str(binary),
                    str(table),
                    str(ROOT / "build/bbc/disc.ssd"),
                    str(multiplier),
                    str(count),
                    str(broken),
                ],
                capture_output=True,
                text=True,
                timeout=900,
            )
            control_log = case / "last-media.log"
            control_log.write_text(control.stdout + control.stderr)
            if (
                not fdc_concurrent.media_mismatch(control, NAMES[-1])
                or "cell 249999 " not in control.stdout
            ):
                raise ValueError("Final media leaf was not checked")
            checks.append(
                {"name": "last-media-cell", "clock_multiplier": multiplier, "accepted": True}
            )
            artifacts.extend([control_log, *[broken / f"media-{i}.bin" for i in range(count)]])
    return checks, artifacts


KERNEL_TOP = """
module FdcCrcSourceProbe(input[255:0]registers,input[53:0]stateCount,input[31:0]onDiscCrc,input[15:0]crc,input[7:0]byteValue,callContext,pointer,status,
output[255:0]followingRegisters,output[53:0]followingCount,output[31:0]followingCrc,output finished,completed,seeking,stateWritten,output[7:0]finishResult,seekContext,nextState,effectCount,followingPointer,followingStatus,statusValue,commandStatus,output stopping);
FdcIdCrcSource body(.registers(registers),.stateCount(stateCount),.onDiscCrc(onDiscCrc),.crc(crc),.byteValue(byteValue),.callContext(callContext),.followingRegisters(followingRegisters),.followingCount(followingCount),.followingCrc(followingCrc),.finished(finished),.completed(completed),.seeking(seeking),.stateWritten(stateWritten),.finishResult(finishResult),.seekContext(seekContext),.nextState(nextState),.effectCount(effectCount));
FdcHeaderStopSource header(.status(status),.pointer(pointer),.followingStatus(followingStatus),.followingPointer(followingPointer),.stopping(stopping));
FdcStatusReadSource readStatus(.status(status),.value(statusValue));
FdcCommandStatusSource acceptCommand(.status(status),.value(commandStatus));
endmodule
"""


def kernel_fault(source, name):
    matches = re.findall(r"(?ms)^module FdcIdCrcSource\(.*?^endmodule", source)
    if len(matches) != 1:
        raise ValueError("Changed original CRC body module")
    body = matches[0]
    if name == "raw-command":
        changed = replace_once(
            body, "({56'd0,r31} == 64'h18)", "(({56'd0,r31} & 64'h3c) == 64'h18)"
        )
    elif name == "crc":
        changed = replace_once(
            body, "({48'd0,crc} == jsInt({32'd0,onCrc}))", "({48'd0,crc} != jsInt({32'd0,onCrc}))"
        )
    elif name == "wrapped-age":
        targets = re.findall(r"(?m)^count = (local[0-9]+)\[53:0\];$", body)
        if len(targets) != 1:
            raise ValueError("Changed original CRC Number increment")
        changed = replace_once(
            body, "count = " + targets[0] + "[53:0];", "count = " + targets[0] + "[7:0];"
        )
    else:
        raise ValueError("Unknown CRC kernel fault")
    return replace_once(source, body, changed)


def kernel_checks():
    source = (DIRECTORY / "source.v").read_text()
    checks, artifacts = [], []
    for name in ("positive", "raw-command", "crc", "wrapped-age"):
        case = DIRECTORY / ("kernel-" + name)
        case.mkdir(exist_ok=True)
        model = case / "actual.v"
        model.write_text(
            (source if name == "positive" else kernel_fault(source, name)) + KERNEL_TOP
        )
        binary = callback_order.compile_native(
            model, case, "FdcCrcSourceProbe", ROOT / "sim/native/bbc_fdc_crc_source_test.cpp"
        )
        trial = subprocess.run(
            [str(binary), str(DIRECTORY)], capture_output=True, text=True, timeout=120
        )
        log = case / "run.log"
        log.write_text(trial.stdout + trial.stderr)
        accepted = (
            (trial.returncode == 0 and not trial.stderr and trial.stdout == SUMMARY)
            if name == "positive"
            else (
                trial.returncode == 1
                and not trial.stderr
                and re.fullmatch(r"ID_CRC_SOURCE_MISMATCH record [0-9]+\n", trial.stdout)
                is not None
            )
        )
        if not accepted:
            raise ValueError("Original CRC kernel differs: " + str(log))
        checks.append({"name": "kernel-" + name, "accepted": True})
        artifacts.extend([model, binary, log, case / "build.json", case / "build.log"])
        if name != "positive":
            continue
        for table, expected in (
            ("original.bin", "ID_CRC_SOURCE_MISMATCH record 1189887\n"),
            ("header.bin", "ID_STOP_SOURCE_MISMATCH record 1023\n"),
        ):
            broken = case / ("last-" + table.removesuffix(".bin"))
            broken.mkdir(exist_ok=True)
            fdc_concurrent.stage_fixture_fault(DIRECTORY, broken, ("original.bin", "header.bin", "status.bin"), table)
            control = subprocess.run(
                [str(binary), str(broken)], capture_output=True, text=True, timeout=120
            )
            control_log = broken / "run.log"
            control_log.write_text(control.stdout + control.stderr)
            if control.returncode != 1 or control.stderr or control.stdout != expected:
                raise ValueError("Final CRC source record was not checked")
            checks.append({"name": "last-" + table, "accepted": True})
            artifacts.extend(
                [control_log, *[broken / n for n in ("original.bin", "header.bin", "status.bin")]]
            )
    return checks, artifacts


CONTINUATION = """
module IdCrcContinuation(input[255:0]registers,input[53:0]count,input[31:0]onCrc,input[15:0]crc,input[7:0]byteValue,callContext,output closure,entry);
wire[255:0]nextRegisters;wire[53:0]nextCount;wire[31:0]nextCrc;wire finished,completed,seeking,stateWritten;wire[7:0]effectCount;
FdcIdCrcSource source(.registers(registers),.stateCount(count),.onDiscCrc(onCrc),.crc(crc),.byteValue(byteValue),.callContext(callContext),.followingRegisters(nextRegisters),.followingCount(nextCount),.followingCrc(nextCrc),.finished(finished),.completed(completed),.seeking(seeking),.stateWritten(stateWritten),.effectCount(effectCount));
assign closure=count<2 || count>54'h20000000000000 || (nextCount>2 && nextCount<=54'h20000000000000 && nextRegisters==registers && !finished && !completed && !seeking && !stateWritten && effectCount==0);
assign entry=count!=1 || nextCount==2;
endmodule
"""


def continuation():
    checks, artifacts = [], []
    source = (DIRECTORY / "source.v").read_text()
    for name in ("positive", "wrapped-age"):
        case = DIRECTORY / ("continuation-" + name)
        case.mkdir(exist_ok=True)
        model = case / "actual.v"
        model.write_text(
            (source if name == "positive" else kernel_fault(source, name)) + CONTINUATION
        )
        graph = case / "graph.json"
        prepared.prepare(
            case,
            f"read_verilog {model}; prep -top IdCrcContinuation -flatten; opt; check -assert; write_json {graph}",
            [model],
            [graph],
        )
        callback_cpu.guard_inputs(
            json.loads(graph.read_text()),
            "IdCrcContinuation",
            {
                "registers": 256,
                "count": 54,
                "onCrc": 32,
                "crc": 16,
                "byteValue": 8,
                "callContext": 8,
            },
        )
        for prop in ("closure", "entry") if name == "positive" else ("closure",):
            query = case / prop
            query.mkdir(exist_ok=True)
            artifacts.extend(
                callback_cpu.prove(
                    query,
                    f"read_json {graph}; opt_clean -purge",
                    prop,
                    name == "positive",
                    horizon=0,
                    show="-show-inputs",
                )
            )
            checks.append({"name": "continuation-" + name, "property": prop, "accepted": True})
        artifacts.extend([model, graph, case / "prepared.json", case / "prepare.log"])
    return checks, artifacts


def original(path):
    report = json.loads(path.read_text())
    cases = report.get("cases", [])
    if report.get("passed") is not True or [c.get("name") for c in cases] != NAMES:
        raise ValueError("Incomplete original CRC history inventory")
    for c in cases:
        name = c["name"]
        if type(c.get("rows")) is not int or c["rows"] <= 0:
            raise ValueError("Invalid original CRC observations")
        if name.startswith("search-id-command-"):
            if (
                type(c.get("command")) is not int
                or type(c.get("offset")) is not int
                or type(c.get("deadline")) is not int
                or not 850000 < c["deadline"] < 1500000
                or name != f"search-id-command-{c['command']}-{c['offset']}"
            ):
                raise ValueError("Changed live-command CRC witness")
            continue
        if type(c.get("first")) is not int or not 850000 < c["first"] < 1500000:
            raise ValueError("Invalid original CRC entry")
        if name == "retained-write-context":
            if c.get("deadline") != c["first"] + 640:
                raise ValueError("Changed retained-context witness")
            continue
        if type(c.get("mode")) is not int:
            raise ValueError("Invalid programmed IRQ mode")
        if name.startswith("crc-irq-"):
            if (
                type(c.get("ack")) is not bool
                or type(c.get("offset")) is not int
                or c.get("deadline") != c["first"] + 512
                or name != f"crc-irq-{c['mode']}-{int(c['ack'])}-{c['offset']}"
            ):
                raise ValueError("Changed CRC IRQ witness")
        elif name.startswith("header-irq-"):
            if (
                type(c.get("ack")) is not bool
                or c.get("deadline") != c["first"] + 128
                or name != f"header-irq-{c['mode']}-{int(c['ack'])}"
            ):
                raise ValueError("Changed header IRQ witness")
        elif name.startswith("header-stop-"):
            if c.get("deadline") != c["first"] + 128 or name != f"header-stop-{c['mode']}":
                raise ValueError("Changed header stop witness")
        else:
            prefix = (
                "id-retry-irq-"
                if name.startswith("id-retry-irq-")
                else "id-retries-"
                if name.startswith("id-retries-")
                else "id-live-"
            )
            if name != prefix + str(c["mode"]):
                raise ValueError("Changed ID retry witness")
    return cases


def qualify():
    current_machine.audit()
    DIRECTORY.mkdir(parents=True, exist_ok=True)
    clocks = [current_machine.DIRECTORY / f"clock{m}/result.json" for m in (1, 3)]
    premises = [fdc_byte.DIRECTORY / "result.json", fdc_bits.DIRECTORY / "result.json"]
    for path in [*clocks, *premises]:
        memory_composition.sealed(path)
    reference = source_root("jsbeeb")
    compiler = ROOT / "tools/bbc/fdc_crc_source.mjs"
    helper = Path(__file__).with_suffix(".mjs")
    files = [
        Path(__file__),
        helper,
        compiler,
        ROOT / "tools/bbc/fdc_byte_source.mjs",
        ROOT / "sim/native/bbc_fdc_crc_source_test.cpp",
        ROOT / "sim/native/bbc_fdc_branch_test.cpp",
        ROOT / "sim/native/bbc_disc_payload.h",
        ROOT / "sim/native/bbc_fm_media.h",
        ROOT / "tools/bbc/reference_drive.mjs",
        ROOT / "build/bbc/disc.ssd",
        ROOT / "node_modules/prettier/plugins/babel.mjs",
        *[ROOT / n for n in current_machine.generation_inputs()],
        current_machine.DIRECTORY / "generation.json",
        *clocks,
        *premises,
        *[p for _, p, _ in current_machine.parents()],
        *[b for _, p, _ in current_machine.parents() for b in p.parent.glob("*.bin")],
        *sorted((reference / "src").rglob("*.js")),
        *[
            Path(m.__file__)
            for m in (
                current_machine,
                memory_composition,
                callback_cpu,
                callback_order,
                fdc_byte,
                fdc_bits,
                fdc_concurrent,
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
    subprocess.run(["node", str(compiler), str(reference), str(DIRECTORY)], check=True, timeout=180)
    projection = json.loads((DIRECTORY / "projection.json").read_text())
    if (
        projection.get("passed") is not True
        or [projection.get(k) for k in ("crc_cases", "header_cases", "status_cases", "registers")]
        != [1189888, 1024, 256, 32]
        or not (DIRECTORY / "source.v")
        .read_text()
        .startswith((fdc_byte.DIRECTORY / "source.v").read_text())
    ):
        raise ValueError("Incomplete original CRC source projections")
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
        timeout=180,
    )
    cases = original(DIRECTORY / "original.json")
    selected = fdc_events.fixtures((DIRECTORY / "original.tsv").read_text(), NAMES)
    media = [DIRECTORY / f"media-{i}.bin" for i in range(len(NAMES))]
    if any(p.stat().st_size != 500000 for p in media) or any(
        int(selected[c["name"]].splitlines()[0].split()[-1]) != c["rows"] for c in cases
    ):
        raise ValueError("Incomplete CRC observations or final media")
    checks, artifacts = (
        [],
        [
            DIRECTORY / n
            for n in (
                "source.v",
                "projection.json",
                "original.bin",
                "header.bin",
                "status.bin",
                "original.json",
                "original.tsv",
            )
        ],
    )
    artifacts.extend(media)
    for producer in (kernel_checks, continuation):
        c, a = producer()
        checks.extend(c)
        artifacts.extend(a)
    for m, p, t in current_machine.parents():
        for producer in (
            lambda: formal(m, p, t),
            lambda: native(m, p, t, sum(c["rows"] for c in cases)),
        ):
            c, a = producer()
            checks.extend(c)
            artifacts.extend(a)
    current_machine.audit()
    for path in [*clocks, *premises]:
        memory_composition.sealed(path)
    if before != hashes(files):
        raise ValueError("ID CRC qualification inputs changed")
    write_json(
        DIRECTORY / "result.json",
        {
            "passed": True,
            "inputs_unchanged": True,
            "inputs": before,
            "artifacts": hashes(artifacts),
            "checks": checks,
            "register_bits": WIDTH,
            "histories": len(NAMES),
            "observations_per_clock": sum(c["rows"] for c in cases),
            "full_equivalence": False,
            "universal_game_equivalence": False,
            "scope": "Original ID CRC body, default ID decrement/stop tail and typed status-register read projections. The body records exactly one delegated completion, seek, state-change or finish call per decision; callee effects remain API boundaries. Source Number continuation is proved through the 2^53 plateau. Actual controller properties bind error/completion decisions, moved retries, retained write context and counter/gap setup, sector wait, later byte callbacks, header/CRC IRQ prefixes, decision coverage and complete status snapshots including bit 6. Public cold histories cover live command changes, IRQ/no-DMA modes at CRC and header deadlines, later retries, and retained write context. Every final raw medium cell is compared. Kernel, wrapped-age, actual RTL and last-record/last-media controls must fail.",
            "limits": "These are local transition properties under loaded lower-drive, aligned standard-clock word frames, default distinct header destinations and no simultaneous host/index/earlier mechanical callback. Completion is bound for ready final sectors; retry allocation is bound for nonzero moved targets. Counter entry/continuation and API flags precede delegated callee effects. The complete initialized source/controller relation, arbitrary header pointers/bit framing, missing-clock and weak-cell behavior, live gap counters and completion redispatch, queued timer callbacks, formatting, traps and whole-machine/game equivalence remain open.",
        },
    )


if __name__ == "__main__":
    qualify()
