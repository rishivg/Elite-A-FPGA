"""Qualify live data-marker decisions and canonical result-register writes."""

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

DIRECTORY = ROOT / "build/bbc/fdc-source-marker"
WIDTH = 934
FIELDS = {**fdc_byte.FIELDS, "dataSyncZeros": 2, "readCrc": 16}


EXPORTS = {
    "cell": ("cellBoundary", 1),
    "present": ("headerPresent", 1),
    "indexPulse": ("index", 1),
    "writeByte": ("isWrite", 1),
    "idByte": ("isId", 1),
    "specialStarts": ("writeSpecialStarts", 1),
    "specialTarget": ("writeSpecialTarget", 8),
    "specialValue": ("startParameters_1", 8),
}

PROPERTIES = (
    "irqEquivalent",
    "resultEquivalent",
    "crcEquivalent",
    "clockEquivalent",
    "resultWriteEquivalent",
)


FAULTS = {
    "owner": ("normal-marker-83-28--1", "irqEquivalent"),
    "deleted-mask": ("deleted-marker-83-17--1", "irqEquivalent"),
    "result-or": ("deleted-result--1", "resultEquivalent"),
    "result-write": ("deleted-result-0", "resultWriteEquivalent"),
}
NAMES = [
    f"{kind}-marker-{op}-{cmd}-{offset}"
    for kind in ("normal", "deleted")
    for op in (83, 95)
    for cmd in ((28, 29, 92, 93) if kind == "normal" else (16, 17, 80, 81))
    for offset in (-1, 0, 1)
] + [f"deleted-result-{offset}" for offset in (-1, 0, 1)]
SUMMARY = "Original marker: 16777216 decisions; 256 result writes; 131072 internal result sets; 256 IRQ starts; 512 external snapshots; 0 differences\n"


def fault(source, name):
    targets = {
        "owner": ("(command != 8'h1c)", "(! isVerify)"),
        "deleted-mask": ("(command[3 : 0] != 4'b0000)", "(command[3 : 2] != 2'b00)"),
        "result-or": ("eventResult = 8'h20;", "eventResult = (result | 8'h20);"),
        "result-write": ("hostResult = startParameters_1;", "hostResult = eventResult;"),
    }
    if name not in targets:
        raise ValueError("Unknown marker mutation")
    a, b = targets[name]
    if source.count(a) != 1:
        raise ValueError("Changed marker mutation target: " + name)
    return source.replace(a, b)


def indices(body):
    state = body["ports"]["proofState"]["bits"]
    if (
        len(state) != WIDTH
        or len(set(state)) != WIDTH
        or len(body["ports"]["proofNext"]["bits"]) != WIDTH
    ):
        raise ValueError("Incomplete marker controller state")
    fields, used = {}, set()
    for n, w in FIELDS.items():
        b = body["netnames"][n]["bits"]
        if len(b) != w or not set(b).issubset(state) or used.intersection(b):
            raise ValueError("Changed marker field: " + n)
        fields[n] = [state.index(v) for v in b]
        used.update(b)
    return fields


def contract(body, enable):
    ix = indices(body)

    def q(n, v="proofState"):
        return "{" + ",".join(f"{v}[{i}]" for i in reversed(ix[n])) + "}"

    inputs = []
    ports = []
    for n, p in body["ports"].items():
        if p["direction"] == "input":
            inputs.append(f"input[{len(p['bits']) - 1}:0]{n}")
            ports.append(f".{n}({n})")
    ports.extend(f".probe_{n}({n})" for n in EXPORTS)
    return f"""module MarkerBinding({",".join(inputs)},output {",".join(PROPERTIES)});
wire[7:0]busValue;wire[933:0]following;wire cell,present,indexPulse,writeByte,idByte,specialStarts;wire[7:0]specialTarget,specialValue;
FdcStateNext actual({",".join(ports)},.proofNext(following),.io_result(busValue));
wire starting,resultWritten,stateWritten,finished,crcWritten;wire[7:0]resultValue,nextState,finishResult;
FdcMarkerSource source(.commandByte({q("command")}),.clockByte(io_storageData[15:8]),.dataByte(io_storageData[7:0]),.starting(starting),.resultWritten(resultWritten),.stateWritten(stateWritten),.finished(finished),.crcWritten(crcWritten),.resultValue(resultValue),.nextState(nextState),.finishResult(finishResult));
wire[31:0]markerCrc;FdcCrcSource crc(.arg0(16'hffff),.arg1(io_storageData[7:0]),.value(markerCrc));
wire[7:0]mode={q("mode")},nextMode={q("mode", "following")},drive={q("driveOut")};
wire frame={enable} && !reset && !io_warmReset && !io_read && !io_write && !indexPulse && !{q("dispatchPending")} && {q("mechanicalPhase")}==0 && {q("phase_1")}==8 && {q("readDataPending")} && !writeByte && !idByte && {q("dataSyncZeros")}==2 && cell && present && !drive[0] && mode[5:4]!=3 && io_storageData!=16'hff00;
wire[7:0]status=(mode & 8'h73)|({q("busy")}?8'h80:0)|({q("interrupt")}?8'h08:0)|({q("request")}?8'h04:0);
wire[7:0]followingStatus=(nextMode & 8'h73)|({q("busy", "following")}?8'h80:0)|({q("interrupt", "following")}?8'h08:0)|({q("request", "following")}?8'h04:0);
wire[7:0]startedStatus;FdcStartIrqSource start(.status(status),.followingStatus(startedStatus));
assign irqEquivalent=!frame || !stateWritten || (nextState==9 && followingStatus==(starting?startedStatus:status) && {q("nmiLevel", "following")}=={q("nmiLevel")});
wire[7:0]setResult;wire setReady;FdcResultSetSource setter(.oldResult({q("result")}),.value(resultValue),.oldReady({q("ready")}),.followingResult(setResult),.followingReady(setReady));
wire[7:0]externalStatus;FdcExternalStatusSource external(.status(followingStatus),.ready(resultWritten?setReady:{q("ready")}),.value(externalStatus));
assign resultEquivalent=!frame || !stateWritten || ({q("result", "following")}==(resultWritten?setResult:{q("result")}) && {q("ready", "following")}==(resultWritten?setReady:{q("ready")}) && (io_address!=0 || busValue==externalStatus));
assign crcEquivalent=!frame || !stateWritten || (crcWritten && {q("phase_1", "following")}==8 && {q("readCrc", "following")}==markerCrc[15:0] && !{q("readDataPending", "following")} && {q("delay", "following")}==2047);
assign clockEquivalent=!frame || !finished || (finishResult==8 && {q("phase_1", "following")}==0 && !{q("busy", "following")} && {q("ready", "following")} && {q("interrupt", "following")} && {q("nmiLevel", "following")} && {q("result", "following")}==({q("result")}|finishResult));
wire[7:0]writtenResult;FdcResultWriteSource writeResult(.value(specialValue),.followingResult(writtenResult));
wire resultWrite={enable} && !reset && !io_warmReset && io_write && specialStarts && specialTarget[5:0]==22;
assign resultWriteEquivalent=!resultWrite || {q("result", "following")}==writtenResult;
endmodule
"""


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
            f"read_json {cut}; read_verilog {DIRECTORY / 'source.v'} {wrapper}; prep -top MarkerBinding -flatten; opt; check -assert; write_json {graph}",
            [cut, DIRECTORY / "source.v", wrapper],
            [graph],
        )
        for prop in PROPERTIES if name == "positive" else (FAULTS[name][1],):
            query = case / prop
            query.mkdir(exist_ok=True)
            slim = query / "graph.json"
            others = " ".join("MarkerBinding/w:" + p for p in PROPERTIES if p != prop)
            prepared.prepare(
                query,
                f"read_json {graph}; delete -output {others}; opt_clean -purge; check -assert; write_json {slim}",
                [graph],
                [slim],
            )
            callback_cpu.guard_inputs(
                json.loads(slim.read_text()),
                "MarkerBinding",
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
            raise ValueError("Marker source history differs: " + str(log))
        artifacts.extend([model, binary, log, case / "build.json", case / "build.log"])
        checks.append({"name": "native-" + name, "clock_multiplier": multiplier, "accepted": True})
        print("Marker", multiplier, name, "PASS", flush=True)
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


KERNEL_TOP = "\nmodule FdcMarkerSourceProbe(input[7:0]commandByte,clockByte,dataByte,value,oldResult,status,input oldReady,output setReady,output starting,resultWritten,stateWritten,finished,crcWritten,output[7:0]resultValue,nextState,finishResult,followingResult,setResult,irqValue,externalValue,output[15:0]markerCrc);\nFdcMarkerSource marker(.commandByte(commandByte),.clockByte(clockByte),.dataByte(dataByte),.starting(starting),.resultWritten(resultWritten),.stateWritten(stateWritten),.finished(finished),.crcWritten(crcWritten),.resultValue(resultValue),.nextState(nextState),.finishResult(finishResult));\nwire[31:0]crcValue;FdcCrcSource crc(.arg0(16'hffff),.arg1(dataByte),.value(crcValue));assign markerCrc=crcWritten?crcValue[15:0]:16'd0;\nFdcExternalStatusSource external(.status(status),.ready(oldReady),.value(externalValue));\nFdcStartIrqSource irq(.status(status),.followingStatus(irqValue));\nFdcResultWriteSource resultWrite(.value(value),.followingResult(followingResult));\nFdcResultSetSource resultSet(.oldResult(oldResult),.value(value),.oldReady(oldReady),.followingResult(setResult),.followingReady(setReady));\nendmodule\n"


def kernel_fault(source, name):
    targets = {
        "raw-command": ("(commandByte == 8'd28)", "((commandByte & 8'h3c) == 8'd28)"),
        "result": ("resultValue=8'd32;", "resultValue=8'd48;"),
        "crc": (".arg0(16'hffff)", ".arg0(16'h0)"),
        "register-write": (
            "module FdcResultWriteSource(input[7:0]value,output[7:0]followingResult);assign followingResult=value;",
            "module FdcResultWriteSource(input[7:0]value,output[7:0]followingResult);assign followingResult=value^8'd1;",
        ),
        "setter-or": (
            "FdcResultSetSource(input[7:0]oldResult,value,input oldReady,output[7:0]followingResult,output followingReady);assign followingResult=value;",
            "FdcResultSetSource(input[7:0]oldResult,value,input oldReady,output[7:0]followingResult,output followingReady);assign followingResult=oldResult|value;",
        ),
        "setter-ready": ("assign followingReady=1'b1;", "assign followingReady=oldReady;"),
        "irq-start": ("status|8'd48", "status&8'd207"),
        "external-status": ("(status & 8'd140)", "(status & 8'd128)"),
    }
    if name not in targets:
        raise ValueError("Unknown marker kernel control")
    return replace_once(source, *targets[name])


def kernel_checks():
    source = (DIRECTORY / "source.v").read_text() + KERNEL_TOP
    checks = []
    artifacts = []
    controls = (
        "raw-command",
        "result",
        "crc",
        "register-write",
        "setter-or",
        "setter-ready",
        "irq-start",
        "external-status",
    )
    leaves = {
        "original.bin": "MARKER_SOURCE_MISMATCH record 16777215\n",
        "result.bin": "RESULT_SOURCE_MISMATCH value 255\n",
        "setter.bin": "SET_RESULT_SOURCE_MISMATCH record 131071\n",
        "irq.bin": "IRQ_START_SOURCE_MISMATCH status 255\n",
        "external.bin": "EXTERNAL_STATUS_SOURCE_MISMATCH record 511\n",
    }
    for name in ("positive", *controls):
        case = DIRECTORY / ("kernel-" + name)
        case.mkdir(exist_ok=True)
        model = case / "actual.v"
        model.write_text(source if name == "positive" else kernel_fault(source, name))
        binary = callback_order.compile_native(
            model, case, "FdcMarkerSourceProbe", ROOT / "sim/native/bbc_fdc_marker_source_test.cpp"
        )
        r = subprocess.run(
            [str(binary), str(DIRECTORY)], capture_output=True, text=True, timeout=120
        )
        log = case / "run.log"
        log.write_text(r.stdout + r.stderr)
        accepted = (
            (r.returncode == 0 and not r.stderr and r.stdout == SUMMARY)
            if name == "positive"
            else (
                r.returncode == 1
                and not r.stderr
                and re.fullmatch(
                    r"(?:MARKER|RESULT|SET_RESULT|IRQ_START|EXTERNAL_STATUS)_SOURCE_MISMATCH (?:record|value|status) [0-9]+\n",
                    r.stdout,
                )
                is not None
            )
        )
        if not accepted:
            raise ValueError("Marker source kernel differs: " + str(log))
        checks.append({"name": "kernel-" + name, "accepted": True})
        artifacts.extend([model, binary, log, case / "build.json", case / "build.log"])
        print("Marker kernel", name, "PASS", flush=True)
        if name != "positive":
            continue
        for table, expected in leaves.items():
            broken = case / ("last-" + table.removesuffix(".bin"))
            broken.mkdir(exist_ok=True)
            fdc_concurrent.stage_fixture_fault(DIRECTORY, broken, leaves, table)
            r = subprocess.run(
                [str(binary), str(broken)], capture_output=True, text=True, timeout=120
            )
            log = broken / "run.log"
            log.write_text(r.stdout + r.stderr)
            if r.returncode != 1 or r.stderr or r.stdout != expected:
                raise ValueError("Unchecked last marker source record: " + table)
            checks.append({"name": "last-" + table, "accepted": True})
            artifacts.extend([log, *[broken / file for file in leaves]])
    return checks, artifacts


def original(path):
    report = json.loads(path.read_text())
    cases = report.get("cases", [])
    if report.get("passed") is not True or [c.get("name") for c in cases] != NAMES:
        raise ValueError("Incomplete original marker histories")
    for c in cases:
        name = c["name"]
        rows = (
            292
            if name.startswith("deleted-result-")
            else (38 if name.startswith("normal-") else 299)
        )
        if (
            type(c.get("rows")) is not int
            or c["rows"] != rows
            or type(c.get("deadline")) is not int
            or c["deadline"] <= 0
            or type(c.get("offset")) is not int
            or c["offset"] not in (-1, 0, 1)
        ):
            raise ValueError("Invalid marker witness coordinates")
        if name.startswith("deleted-result-"):
            if c.get("kind") != "deleted" or name != f"deleted-result-{c['offset']}":
                raise ValueError("Changed result-register deadline")
        elif (
            c.get("kind") not in ("normal", "deleted")
            or type(c.get("operation")) is not int
            or c["operation"] not in (83, 95)
            or type(c.get("command")) is not int
            or name != f"{c['kind']}-marker-{c['operation']}-{c['command']}-{c['offset']}"
        ):
            raise ValueError("Changed live marker command witness")
    return cases


def timelines(cases, selected):
    for c in cases:
        rows = [line.split() for line in selected[c["name"]].splitlines()[1:]]
        programming = c["deadline"] + c["offset"] - 2
        result = c["name"].startswith("deleted-result-")
        expected = [
            (programming, "W", 0, 0x7A),
            (programming + 1, "W", 1, 22 if result else 31),
            (programming + 2, "W", 1, 16 if result else c["command"]),
        ]
        observations = {(int(r[0]), r[1], int(r[2])) for r in rows}
        values = [(int(r[0]), r[1], int(r[2]), int(r[3])) for r in rows]
        if not any(values[i : i + 3] == expected for i in range(len(values) - 2)):
            raise ValueError("Missing marker programming deadline")
        wanted = (
            [(4, "R", 1), (129, "P", 0)]
            if result
            else [(d, "P", 0) for d in (4, 127, 128, 129, 255, 256, 257)]
        )
        if any(
            (c["deadline"] + delta, action, address) not in observations
            for delta, action, address in wanted
        ):
            raise ValueError("Missing marker boundary observation")


def qualify():
    current_machine.audit()
    DIRECTORY.mkdir(parents=True, exist_ok=True)
    clocks = [current_machine.DIRECTORY / f"clock{m}/result.json" for m in (1, 3)]
    premises = [fdc_byte.DIRECTORY / "result.json", fdc_bits.DIRECTORY / "result.json"]
    for path in [*clocks, *premises]:
        memory_composition.sealed(path)
    reference = source_root("jsbeeb")
    compiler = ROOT / "tools/bbc/fdc_marker_source.mjs"
    helper = Path(__file__).with_suffix(".mjs")
    files = [
        Path(__file__),
        helper,
        compiler,
        ROOT / "tools/bbc/fdc_byte_source.mjs",
        fdc_bits.DIRECTORY / "source.v",
        ROOT / "sim/native/bbc_fdc_marker_source_test.cpp",
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
    marker = DIRECTORY / "marker.v"
    marker.write_bytes((DIRECTORY / "source.v").read_bytes())
    (DIRECTORY / "source.v").write_bytes(
        (fdc_bits.DIRECTORY / "source.v").read_bytes() + marker.read_bytes()
    )
    projection = json.loads((DIRECTORY / "projection.json").read_text())
    if projection.get("passed") is not True or [
        projection.get(k)
        for k in (
            "marker_cases",
            "result_cases",
            "setter_cases",
            "irq_cases",
            "external_cases",
            "registers",
        )
    ] != [16777216, 256, 131072, 256, 512, 32]:
        raise ValueError("Incomplete original marker projections")
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
    timelines(cases, selected)
    media = [DIRECTORY / f"media-{i}.bin" for i in range(len(NAMES))]
    if any(p.stat().st_size != 500000 for p in media) or any(
        int(selected[c["name"]].splitlines()[0].split()[-1]) != c["rows"] for c in cases
    ):
        raise ValueError("Incomplete marker observations or final media")
    checks, artifacts = (
        [],
        [
            DIRECTORY / n
            for n in (
                "source.v",
                "projection.json",
                "original.bin",
                "result.bin",
                "setter.bin",
                "irq.bin",
                "external.bin",
                "marker.v",
                "original.json",
                "original.tsv",
            )
        ],
    )
    artifacts.extend(media)
    for producer in (kernel_checks,):
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
        raise ValueError("Marker qualification inputs changed")
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
            "scope": "Original data-marker decisions across all 16777216 command/clock/data words, typed R22 writes, result replacement across all previous/new bytes and ready states, IRQ-start and external-status projections. CRC initialization reuses the sealed original CRC kernel. Five actual-controller properties per clock bind live VERIFY/deleted command bits, result/ready/status snapshots, marker CRC entry and clock-error decisions, plus host special writes to R22. Four RTL controls require formal and exact public mismatches at both clocks. All 51 public cold histories compare every final raw media cell. Kernel, final-record and final-media controls must fail; 44 checks.",
            "limits": "Word frames require a loaded lower drive, recognized default sync, nonzero following word, closed write gate, quiet or partial IRQ mode and no simultaneous host/index/earlier mechanical callback. State-change, finish and transport calls remain API boundaries. The R22 host property binds accepted in-flight WRITE SPECIAL calls. This is not the complete initialized source/controller relation. Arbitrary bit framing, missing clocks, live gap counters, completion redispatch, queued timers, other RF aliases, formatting, source callback causes and full CPU/game/board equivalence remain open.",
        },
    )


if __name__ == "__main__":
    qualify()
