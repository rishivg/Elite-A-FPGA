"""Source byte-control predicates and acknowledgement-independent serial progress."""

import json
import re
import shutil
import subprocess
from pathlib import Path

from tools.bbc.proofs import current_machine, memory_composition
from tools.bbc.proofs.cpu_reference import (
    callback_cpu,
    callback_order,
    fdc_concurrent,
    fdc_epoch,
    fdc_events,
    prepared,
)
from tools.bbc.proofs.video_cursor import hashes, replace_once
from tools.validation.common import ROOT, source_root, write_json

DIRECTORY = ROOT / "build/bbc/fdc-byte-control"
WIDTH = 934
PROPERTIES = (
    "irqEquivalent",
    "lossEquivalent",
    "quietEquivalent",
    "deliveryEquivalent",
    "counterEquivalent",
    "progressEquivalent",
)
FIELDS = {
    "phase_1": 4,
    "mechanicalPhase": 2,
    "mechanicalDelay": 24,
    "dispatchPending": 1,
    "executingOp": 4,
    "command": 8,
    "mode": 8,
    "request": 1,
    "interrupt": 1,
    "busy": 1,
    "ready": 1,
    "data": 8,
    "result": 8,
    "nmiLevel": 1,
    "delay": 24,
    "readDataPending": 1,
    "position": 15,
    "headerPointer": 8,
    "byteCounterLow": 8,
    "byteCounterHigh": 8,
    "register0": 8,
    "rotation": 23,
    "driveOut": 8,
}
EXPORTS = {
    "irq": ("byteIrqs", 1),
    "indexPulse": ("index", 1),
    "cell": ("cellBoundary", 1),
    "present": ("headerPresent", 1),
    "parsing": ("parsingHeader", 1),
    "crcClock": ("crcClock", 23),
    "writeByte": ("isWrite", 1),
    "idByte": ("isId", 1),
    "abort": ("serialAbort", 1),
    "actualLow": ("serialCounterLow", 8),
    "actualHigh": ("serialCounterHigh", 8),
}
FAULTS = {
    "irq-shadow": ("mode-83-129-1--1", "irqEquivalent"),
    "quiet-abort": ("mode-83-128-1--1", "lossEquivalent"),
    "host-coupled-progress": ("last-83--1", "progressEquivalent"),
    "short-read-tail": ("last-83-0", "progressEquivalent"),
    "short-write-tail": ("last-75-0", "progressEquivalent"),
    "wrong-marker-count": ("last-75--1", "counterEquivalent"),
}

NAMES = [
    f"mode-{operation}-{mode}-{ack}-{offset}"
    for operation in (83, 75, 91)
    for mode in (128, 129, 144, 145, 160, 161, 176, 177)
    for ack in (0, 1)
    for offset in (-1, 0, 1)
] + [
    f"last-{operation}-{offset}"
    for operation in (83, 75, 91)
    for offset in (-1, 0, 1, 126, 127, 128, 129, 254, 255, 256, 257, 260)
]


def indices(body):
    state = body["ports"]["proofState"]["bits"]
    if (
        len(state) != WIDTH
        or len(set(state)) != WIDTH
        or len(body["ports"]["proofNext"]["bits"]) != WIDTH
    ):
        raise ValueError("Incomplete byte-control state")
    used = set()
    fields = {}
    for name, width in FIELDS.items():
        bits = body["netnames"][name]["bits"]
        if len(bits) != width or not set(bits).issubset(state) or used.intersection(bits):
            raise ValueError("Changed byte-control field: " + name)
        fields[name] = [state.index(b) for b in bits]
        used.update(bits)
    if "readIrqs" in body["netnames"]:
        raise ValueError("Controller regained a shadow IRQ-enable latch")
    return fields


def fault(source, name):
    target = {
        "irq-shadow": "byteIrqs",
        "quiet-abort": "byteDataLoss",
        "host-coupled-progress": "byteProgress",
        "short-read-tail": "byteTailDelay",
        "short-write-tail": "byteTailDelay",
        "wrong-marker-count": "serialCounterLow",
    }[name]
    matches = re.findall(r"(?m)^  assign " + target + r" = ([^;]+);$", source)
    if len(matches) != 1:
        raise ValueError("Changed byte-control mutation target: " + target)
    rhs = {
        "irq-shadow": "1'b1",
        "quiet-abort": "((! mode[0]) || isScan || request)",
        "host-coupled-progress": "(" + matches[0] + " && (io_read || io_write) && io_address[2])",
        "short-read-tail": "(isWrite ? 24'd10239 : 24'd2047)",
        "short-write-tail": "(isWrite ? 24'd8191 : 24'd4095)",
        "wrong-marker-count": "byteCounterLow",
    }[name]
    return replace_once(
        source,
        "  assign " + target + " = " + matches[0] + ";",
        "  assign " + target + " = " + rhs + ";",
    )


def contract(body, enable):
    fields = indices(body)

    def q(name, vector="proofState"):
        return "{" + ",".join(f"{vector}[{i}]" for i in reversed(fields[name])) + "}"

    inputs = []
    ports = []
    for name, p in body["ports"].items():
        if p["direction"] == "input":
            inputs.append(f"input[{len(p['bits']) - 1}:0]{name}")
            ports.append(f".{name}({name})")
    ports.extend(f".byte_{name}({name})" for name in EXPORTS)
    return f"""module ByteControlBinding({",".join(inputs)},output {",".join(PROPERTIES)});
wire[{WIDTH - 1}:0]following;
wire irq,indexPulse,cell,present,parsing,writeByte,idByte,abort;wire[22:0]crcClock;wire[7:0]actualLow,actualHigh;
FdcStateNext actual({",".join(ports)},.proofNext(following));
wire[7:0]mode={q("mode")},cmd={q("command")},drive={q("driveOut")};
wire[7:0]logicalStatus=(mode & 8'h73) | ({q("request")}?8'd4:8'd0) | ({q("interrupt")}?8'd8:8'd0) | ({q("busy")}?8'd128:8'd0);
wire sourceIrqs,sourceOk,sourceAborted,sourceFinished;wire[7:0]sourceResult;
FdcIrqSource sourceIrq(.low(8'd0),.high(8'd0),.status(logicalStatus),.command(cmd),.value(sourceIrqs));
FdcLossSource sourceLoss(.low(8'd0),.high(8'd0),.status(logicalStatus),.command(cmd),.value(sourceOk),.aborted(sourceAborted),.finished(sourceFinished),.finishResult(sourceResult));
wire marker=writeByte && {q("position")}==0;
wire[7:0]sourceLow=marker?8'd128:{q("byteCounterLow")},sourceHigh=marker?{q("register0")}:{q("byteCounterHigh")};
wire[7:0]nextLow,nextHigh,nextStatus;wire done;
FdcCounterSource sourceCounter(.low(sourceLow),.high(sourceHigh),.status(mode),.command(cmd),.nextLow(nextLow),.nextHigh(nextHigh),.nextStatus(nextStatus),.value(done));
assign irqEquivalent=irq==sourceIrqs;
wire[3:0]owner={q("executingOp")};wire[7:0]pointer={q("headerPointer")};
wire[14:0]position={q("position")};
wire idFrame=!idByte || (position<4 && pointer==8'd12-{{6'd0,position[1:0]}});
wire byteFrame={enable} && !reset && !io_warmReset && !io_read && !io_write && !indexPulse && !{q("dispatchPending")} &&
 ({q("mechanicalPhase")}==0 || {q("mechanicalDelay")}>0) && {q("phase_1")}==8 && {q("delay")}==0 && !{q("readDataPending")} &&
 owner>=2 && owner<=7 && cell && present && io_storageData[15:8]==255 && drive[0]==writeByte && idFrame && (!parsing || {q("rotation")}!=crcClock-23'd1);
wire bad=sourceIrqs && !sourceOk,good=byteFrame && !bad;
assign lossEquivalent=!byteFrame || (abort==bad && (!bad || (sourceAborted && sourceFinished && sourceResult==8'h0a && {q("phase_1", "following")}==0 && {q("mechanicalPhase", "following")}==0 && !{q("busy", "following")} && {q("ready", "following")} && {q("nmiLevel", "following")} && {q("result", "following")}==({q("result")}|sourceResult))));
assign quietEquivalent=!good || sourceIrqs || (!abort && {q("data", "following")}=={q("data")} && {q("nmiLevel", "following")}=={q("nmiLevel")} && {q("request", "following")}=={q("request")} && {q("interrupt", "following")}=={q("interrupt")});
assign deliveryEquivalent=!good || !sourceIrqs || (!abort && {q("nmiLevel", "following")} && {q("request", "following")} && {q("interrupt", "following")} && {q("data", "following")}==(writeByte?{q("data")}:io_storageData[7:0]));
wire[7:0]followingMode={q("mode", "following")};
assign counterEquivalent=!good || idByte || (actualLow==sourceLow && actualHigh==sourceHigh && {q("byteCounterLow", "following")}==nextLow && {q("byteCounterHigh", "following")}==nextHigh && (!done || followingMode[5:4]==nextStatus[5:4]));
wire lastByte=idByte?(pointer[2:0]==1):done;
assign progressEquivalent=!good || ({q("position", "following")}=={q("position")}+15'd1 && {q("phase_1", "following")}==(lastByte?4'd9:4'd8) && {q("delay", "following")}==(lastByte?(writeByte?24'd10239:24'd4095):24'd2047) && (!lastByte || followingMode[5:4]==0));
endmodule
"""


def formal(multiplier, parent, top):
    controller, extra = callback_order.fdc_instance(parent.read_text(), top)
    checks = []
    artifacts = []
    for name in ("positive", *FAULTS):
        case = DIRECTORY / f"formal-{name}{multiplier}"
        case.mkdir(parents=True, exist_ok=True)
        text = controller if name == "positive" else fault(controller, name)
        header = ",".join(
            f"output[{width - 1}:0]byte_{name}" for name, (_, width) in EXPORTS.items()
        )
        text = replace_once(text, "module Intel8271 (", "module Intel8271 (" + header + ",")
        text = replace_once(
            text,
            "endmodule",
            "\n"
            + "".join(f"assign byte_{name}={signal};\n" for name, (signal, _) in EXPORTS.items())
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
            f"read_json {cut}; read_verilog {DIRECTORY / 'source.v'} {wrapper}; prep -top ByteControlBinding -flatten; opt; check -assert; write_json {graph}",
            [cut, DIRECTORY / "source.v", wrapper],
            [graph],
        )
        callback_cpu.guard_inputs(
            json.loads(graph.read_text()),
            "ByteControlBinding",
            {n: len(p["bits"]) for n, p in body["ports"].items() if p["direction"] == "input"},
        )
        for prop in PROPERTIES if name == "positive" else (FAULTS[name][1],):
            query = case / prop
            query.mkdir(exist_ok=True)
            slim = query / "graph.json"
            others = " ".join("ByteControlBinding/w:" + n for n in PROPERTIES if n != prop)
            prepared.prepare(
                query,
                f"read_json {graph}; delete -output {others}; opt_clean -purge; check -assert; write_json {slim}",
                [graph],
                [slim],
            )
            callback_cpu.guard_inputs(
                json.loads(slim.read_text()),
                "ByteControlBinding",
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
            artifacts.extend([slim, query / "prepared.json", query / "prepare.log"])
            checks.append(
                {"clock_multiplier": multiplier, "name": name, "property": prop, "accepted": True}
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
            raise ValueError("Concurrent source history differs: " + str(log))
        artifacts.extend([model, binary, log, case / "build.json", case / "build.log"])
        checks.append({"name": "native-" + name, "clock_multiplier": multiplier, "accepted": True})
        print("Byte control", multiplier, name, "PASS", flush=True)
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
module FdcByteSourceProbe(input[7:0]low,high,status,command,output[7:0]nextLow,nextHigh,nextStatus,output done,irqEnabled,serviceOk,aborted,finished,output[7:0]finishResult);
FdcCounterSource count(.low(low),.high(high),.status(status),.command(command),.nextLow(nextLow),.nextHigh(nextHigh),.nextStatus(nextStatus),.value(done));
FdcIrqSource irq(.low(low),.high(high),.status(status),.command(command),.value(irqEnabled));
FdcLossSource loss(.low(low),.high(high),.status(status),.command(command),.value(serviceOk),.aborted(aborted),.finished(finished),.finishResult(finishResult));
endmodule
"""
SUMMARY = (
    "Original byte control: 256 IRQ, 65536 eligibility, 16777216 counter cases; 0 differences\n"
)


def kernel_fault(source, name):
    module = {"irq": "FdcIrqSource", "loss": "FdcLossSource", "counter": "FdcCounterSource"}[name]
    matches = re.findall(r"(?ms)^module " + module + r"\(.*?^endmodule", source)
    if len(matches) != 1:
        raise ValueError("Changed source byte-kernel module")
    original = matches[0]
    if name == "irq":
        returns = re.findall(r"(?m)^value = ([^;]+);$", original)
        if len(returns) != 1:
            raise ValueError("Changed source IRQ return inventory")
        changed = replace_once(
            original, "value = " + returns[0] + ";", "value = !(" + returns[0] + ");"
        )
    elif name == "loss":
        changed = replace_once(original, "aborted = 1;", "aborted = 0;")
    else:
        changed = replace_once(original, "value = 64'd1 != 0;", "value = 64'd0 != 0;")
    return replace_once(source, original, changed)


def kernel_checks():
    source = (DIRECTORY / "source.v").read_text()
    checks, artifacts = [], []
    for name in ("positive", "irq", "loss", "counter"):
        case = DIRECTORY / ("kernel-" + name)
        case.mkdir(exist_ok=True)
        model = case / "actual.v"
        model.write_text(
            (source if name == "positive" else kernel_fault(source, name)) + KERNEL_TOP
        )
        binary = callback_order.compile_native(
            model, case, "FdcByteSourceProbe", ROOT / "sim/native/bbc_fdc_byte_source_test.cpp"
        )
        trial = subprocess.run(
            [str(binary), str(DIRECTORY)], capture_output=True, text=True, timeout=120
        )
        log = case / "run.log"
        log.write_text(trial.stdout + trial.stderr)
        pattern = {
            "irq": r"BYTE_IRQ_MISMATCH status [0-9]+\n",
            "loss": r"BYTE_LOSS_MISMATCH status [0-9]+ command [0-9]+\n",
            "counter": r"BYTE_COUNTER_MISMATCH status [0-9]+ high [0-9]+ low [0-9]+\n",
        }
        accepted = (
            (trial.returncode == 0 and not trial.stderr and trial.stdout == SUMMARY)
            if name == "positive"
            else (
                trial.returncode == 1
                and not trial.stderr
                and re.fullmatch(pattern[name], trial.stdout) is not None
            )
        )
        if not accepted:
            raise ValueError("Original byte-control kernel differs: " + str(log))
        checks.append({"name": "kernel-" + name, "accepted": True})
        artifacts.extend([model, binary, log, case / "build.json", case / "build.log"])
        if name == "positive":
            broken = case / "last-record"
            broken.mkdir(exist_ok=True)
            for n in ("irq.bin", "loss.bin", "counter.bin"):
                shutil.copyfile(DIRECTORY / n, broken / n)
            with (broken / "counter.bin").open("r+b") as output:
                output.seek(-1, 2)
                byte = output.read(1)[0]
                output.seek(-1, 2)
                output.write(bytes([byte ^ 1]))
            control = subprocess.run(
                [str(binary), str(broken)], capture_output=True, text=True, timeout=120
            )
            control_log = case / "last-record.log"
            control_log.write_text(control.stdout + control.stderr)
            if (
                control.returncode != 1
                or control.stderr
                or control.stdout != "BYTE_COUNTER_MISMATCH status 255 high 255 low 255\n"
            ):
                raise ValueError("Last original counter record was not checked")
            checks.append({"name": "last-counter-record", "accepted": True})
            artifacts.extend(
                [control_log, *[broken / n for n in ("irq.bin", "loss.bin", "counter.bin")]]
            )
    return checks, artifacts


def original(path):
    report = json.loads(path.read_text())
    cases = report.get("cases", [])
    if report.get("passed") is not True or [c.get("name") for c in cases] != NAMES:
        raise ValueError("Incomplete original byte-control history inventory")
    for c in cases:
        if (
            type(c.get("rows")) is not int
            or c["rows"] <= 0
            or type(c.get("first")) is not int
            or not 850000 < c["first"] < 1500000
            or type(c.get("operation")) is not int
            or type(c.get("offset")) is not int
        ):
            raise ValueError("Changed original byte-control witness")
        if c.get("kind") == "mode":
            if (
                type(c.get("mode")) is not int
                or type(c.get("ack")) is not bool
                or c.get("deadline") != c["first"] + 128
                or c["name"] != f"mode-{c['operation']}-{c['mode']}-{int(c['ack'])}-{c['offset']}"
            ):
                raise ValueError("Changed IRQ-mode witness")
        elif c.get("kind") == "last":
            count = 4 if c["operation"] == 91 else 256
            if (
                c.get("count") != count
                or c.get("last") != c["first"] + (count - 1) * 128
                or c["name"] != f"last-{c['operation']}-{c['offset']}"
            ):
                raise ValueError("Changed final-byte witness")
        else:
            raise ValueError("Unknown byte-control history")
    return cases


def qualify():
    current_machine.audit()
    DIRECTORY.mkdir(parents=True, exist_ok=True)
    clocks = [current_machine.DIRECTORY / f"clock{m}/result.json" for m in (1, 3)]
    for path in clocks:
        memory_composition.sealed(path)
    reference = source_root("jsbeeb")
    helper = Path(__file__).with_suffix(".mjs")
    compiler = ROOT / "tools/bbc/fdc_byte_source.mjs"
    files = [
        Path(__file__),
        helper,
        compiler,
        ROOT / "sim/native/bbc_fdc_byte_source_test.cpp",
        ROOT / "sim/native/bbc_fdc_branch_test.cpp",
        ROOT / "sim/native/bbc_disc_payload.h",
        ROOT / "sim/native/bbc_fm_media.h",
        ROOT / "tools/bbc/reference_drive.mjs",
        ROOT / "build/bbc/disc.ssd",
        ROOT / "node_modules/prettier/plugins/babel.mjs",
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
                callback_cpu,
                callback_order,
                fdc_epoch,
                fdc_events,
                fdc_concurrent,
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
    if projection.get("passed") is not True or [
        projection.get(k) for k in ("irq_cases", "loss_cases", "counter_cases")
    ] != [256, 65536, 16777216]:
        raise ValueError("Incomplete original byte-control truth tables")
    # Prototype symlinks are replaced by self-contained qualification artifacts.
    for p in [
        DIRECTORY / "original.tsv",
        DIRECTORY / "original.json",
        *[DIRECTORY / f"media-{i}.bin" for i in range(len(NAMES))],
    ]:
        if p.is_symlink():
            p.unlink()
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
        raise ValueError("Incomplete byte-control observations or media")
    checks, artifacts = kernel_checks()
    artifacts.extend(
        [
            DIRECTORY / n
            for n in (
                "source.v",
                "projection.json",
                "irq.bin",
                "loss.bin",
                "counter.bin",
                "original.tsv",
                "original.json",
            )
        ]
    )
    artifacts.extend(media)
    for multiplier, parent, top in current_machine.parents():
        for work in (
            lambda: formal(multiplier, parent, top),
            lambda: native(multiplier, parent, top, sum(c["rows"] for c in cases)),
        ):
            c, a = work()
            checks.extend(c)
            artifacts.extend(a)
    current_machine.audit()
    if before != hashes(files):
        raise ValueError("Byte-control qualification inputs changed")
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
            "scope": "AST-lowered original IRQ getter, data-loss eligibility with explicit abort/finish API boundaries, and typed byte-counter transitions. Independent original execution checks every 8-bit status, every status/raw-command eligibility pair, and all 16777216 low/high/status counter inputs. Six all-state actual-controller properties at both clock rates bind IRQ eligibility, quiet/delivering/aborting body callbacks, counter effects and host-independent entry into two/five-cell CRC tails. Public histories cover 144 IRQ-mode changes and 36 final-byte acknowledgement deadlines, including CRC boundary samples and complete final-media comparison. Six RTL faults require formal and initialized public mismatches, three kernel faults must fail, and the last counter-oracle record and last final-media cell must be checked.",
            "limits": "Actual byte-step domains require standard-clock loaded drive-0 body words with no simultaneous host/index/earlier mechanical callback, a matching write gate and the four-byte default ID-header pointer frame. Eligibility effects stop at abort/finish calls; this is not a full lowering of those methods. Complete source/controller state correspondence, arbitrary header pointers/bit framing, live-command ID-CRC routing, overlong reprogrammed transfers, formatting, weak cells and whole-machine/game equivalence remain open.",
        },
    )


if __name__ == "__main__":
    qualify()
