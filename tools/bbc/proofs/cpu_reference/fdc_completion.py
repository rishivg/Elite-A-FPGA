"""Qualify typed sector completion and live SPECIFY redispatch."""

import json
import re
import shutil
import subprocess
from pathlib import Path

from tools.bbc.proofs import current_machine, memory_composition
from tools.bbc.proofs.cpu_reference import (
    callback_cpu,
    callback_order,
    fdc_byte,
    fdc_concurrent,
    fdc_epoch,
    fdc_events,
    prepared,
)
from tools.bbc.proofs.video_cursor import hashes, replace_once
from tools.validation.common import ROOT, source_root, write_json

DIRECTORY = ROOT / "build/bbc/fdc-source-completion"
WIDTH = 934
FIELDS = {
    **fdc_byte.FIELDS,
    "readCrc": 16,
    "parameters_0": 8,
    "parameters_1": 8,
    "parameters_2": 8,
    "parameters_3": 8,
    "parameterCount": 8,
    "specifying": 1,
    "acceptingParameters": 1,
    "indexCallback": 2,
    "readCrcHigh": 8,
    "latchedReady": 1,
}
EXPORTS = {
    "writeByte": ("isWrite", 1),
    "idByte": ("isId", 1),
    "indexExpires": ("indexExpires", 1),
    "headerIrqLoss": ("headerIrqLoss", 1),
    "driveIn": ("driveIn", 8),
    "indexPulse": ("index", 1),
}
PROPERTIES = ("dispatchEquivalent", "arithmeticEquivalent", "counterEquivalent", "hostEquivalent")
FAULTS = {
    "redispatch": ("completion-specify-2-0-register", "dispatchEquivalent"),
    "pointer": ("completion-specify-2-0-register", "hostEquivalent"),
    "count": ("completion-specify-2-0-continuation", "hostEquivalent"),
}
NAMES = [
    f"completion-specify-{count}-{offset}-{observation}"
    for count in (2, 34, 66)
    for offset in (-1, 0, 1)
    for observation in ("register", "continuation")
]
SUMMARY = "Original completion: 33554432 decisions; 256 SPECIFY dispatches; 0 differences\n"


def fault(source, name):
    if name == "redispatch":
        pattern = r"(?m)^(  assign when_Intel8271_l\d+(?:_\d+)? = )\(op == 4'b1101\);$"
        source, n = re.subn(pattern, lambda x: x[1] + "1'b0;", source)
        if n != 3:
            raise ValueError("Changed SPECIFY redispatch targets")
        return source
    a, b = {
        "pointer": ("eventRegister0 = parameters_0;", "eventRegister0 = register0;"),
        "count": ("eventParameterCount = 8'h03;", "eventParameterCount = parameterCount;"),
    }[name]
    if source.count(a) != 3:
        raise ValueError("Changed SPECIFY staging targets: " + name)
    source = source.replace(a, b)
    if name == "pointer":
        pattern = r"(specifying <= 1'b1;\n\s+)register0 <= parameters_0;"
        source, n = re.subn(pattern, lambda x: x[1] + "register0 <= register0;", source)
    else:
        pattern = (
            r"(specifying <= 1'b1;\n\s+register0 <= parameters_0;\n\s+)parameterCount <= 8'h03;"
        )
        source, n = re.subn(pattern, lambda x: x[1] + "parameterCount <= parameterCount;", source)
    if n != 3:
        raise ValueError("Changed SPECIFY register targets: " + name)
    return source


def indices(body):
    state = body["ports"]["proofState"]["bits"]
    if (
        len(state) != WIDTH
        or len(set(state)) != WIDTH
        or len(body["ports"]["proofNext"]["bits"]) != WIDTH
    ):
        raise ValueError("Incomplete completion controller state")
    fields, used = {}, set()
    for n, w in FIELDS.items():
        b = body["netnames"][n]["bits"]
        if len(b) != w or not set(b).issubset(state) or used.intersection(b):
            raise ValueError("Changed completion field: " + n)
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
    return f"""module CompletionBinding({",".join(inputs)},output {",".join(PROPERTIES)});
wire[933:0]following;wire writeByte,idByte,indexExpires,headerIrqLoss,indexPulse;wire[7:0]driveIn;
FdcStateNext actual({",".join(ports)},.proofNext(following));
wire[7:0]countNext,sectorNext,driveMask,finishResult,pointerNext,paramNext,callbackNext;wire[15:0]callOrder;
FdcCompletionSource source(.ready(driveIn[2] && {q("latchedReady")}),.countByte({q("parameters_2")}),.sectorByte({q("parameters_1")}),.stepByte({q("parameters_3")}),.followingCount(countNext),.followingSector(sectorNext),.driveMask(driveMask),.finishResult(finishResult),.callOrder(callOrder));
FdcCompletionSpecifySource specCase(.parameterByte({q("parameters_0")}),.pointer(pointerNext),.parameterCount(paramNext),.callback(callbackNext));
wire[7:0]commandByte={q("command")};wire[7:0]drive={q("driveOut")},nextDrive={q("driveOut", "following")};
wire frame={enable} && !reset && !io_warmReset && !io_read && !{q("dispatchPending")} && {q("mechanicalPhase")}==0 && {q("phase_1")}==9 && {q("delay")}==0 && !indexPulse && !indexExpires && !headerIrqLoss && !writeByte && !idByte && !drive[0] && drive[3] && drive[7:6]==1 && io_storageData[15:8]==8'hff && {q("readCrc")}=={{{q("readCrcHigh")},io_storageData[7:0]}} && (commandByte & 8'h3c)==8'h34 && callOrder==16'h1235;
wire quiet=frame && !io_write;
assign dispatchEquivalent=!quiet || ({q("phase_1", "following")}==0 && {q("mechanicalPhase", "following")}==0 && !{q("dispatchPending", "following")} && {q("indexCallback", "following")}==0 && !{q("acceptingParameters", "following")} && {q("specifying", "following")} && {q("register0", "following")}==pointerNext && {q("parameterCount", "following")}==paramNext && callbackNext==2 && {q("busy", "following")}=={q("busy")}  && {q("ready", "following")}=={q("ready")}  && {q("result", "following")}=={q("result")}  && {q("request", "following")}=={q("request")}  && {q("interrupt", "following")}=={q("interrupt")}  && {q("nmiLevel", "following")}=={q("nmiLevel")}  && {q("mode", "following")}=={q("mode")} );
assign arithmeticEquivalent=!quiet || ({q("parameters_2", "following")}==countNext && {q("parameters_1", "following")}==sectorNext);
assign counterEquivalent=!quiet || ({q("byteCounterLow", "following")}=={q("byteCounterLow")} && {q("byteCounterHigh", "following")}=={q("byteCounterHigh")} && !(nextDrive & driveMask));
wire host=frame && io_write && io_address==1 && {q("parameters_0")}==5;
assign hostEquivalent=!host || ({q("register0", "following")}==6 && {q("parameterCount", "following")}==2 && {q("parameters_2", "following")}==io_data && {q("parameters_1", "following")}==sectorNext && {q("specifying", "following")});
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
            f"read_json {cut}; read_verilog {DIRECTORY / 'source.v'} {wrapper}; prep -top CompletionBinding -flatten; opt; check -assert; write_json {graph}",
            [cut, DIRECTORY / "source.v", wrapper],
            [graph],
        )
        for prop in PROPERTIES if name == "positive" else (FAULTS[name][1],):
            query = case / prop
            query.mkdir(exist_ok=True)
            slim = query / "graph.json"
            others = " ".join("CompletionBinding/w:" + p for p in PROPERTIES if p != prop)
            prepared.prepare(
                query,
                f"read_json {graph}; delete -output {others}; opt_clean -purge; check -assert; write_json {slim}",
                [graph],
                [slim],
            )
            callback_cpu.guard_inputs(
                json.loads(slim.read_text()),
                "CompletionBinding",
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
            raise ValueError("Completion source history differs: " + str(log))
        artifacts.extend([model, binary, log, case / "build.json", case / "build.log"])
        checks.append({"name": "native-" + name, "clock_multiplier": multiplier, "accepted": True})
        print("Completion", multiplier, name, "PASS", flush=True)
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


KERNEL_TOP = """module FdcCompletionSourceProbe(input ready,input[7:0]countByte,sectorByte,stepByte,parameterByte,output[7:0]followingCount,followingSector,driveMask,finishResult,pointer,parameterCount,callback,output[15:0]callOrder);
FdcCompletionSource c(.*);FdcCompletionSpecifySource s(.*);endmodule
"""
KERNEL_FAULTS = {
    "ready": ("if(ready)begin", "if(1'b1)begin"),
    "count": ("-32'sd1", "-32'sd2"),
    "mask": ("32'd31", "32'd255"),
    "increment": ("32'd63", "32'd255"),
    "order": ("callOrder=(callOrder<<4)|16'd3;", "callOrder=(callOrder<<4)|16'd2;"),
    "pointer": ("pointer=parameterByte;", "pointer=parameterByte+1;"),
    "parameters": ("parameterCount=8'd3;", "parameterCount=8'd2;"),
    "callback": ("callback=8'd2;", "callback=8'd1;"),
}


def kernel_fault(source, name):
    if name not in KERNEL_FAULTS:
        raise ValueError("Unknown completion kernel mutation")
    return replace_once(source, *KERNEL_FAULTS[name])


def kernel_checks():
    source = (DIRECTORY / "source.v").read_text() + KERNEL_TOP
    checks, artifacts = [], []
    for name in ("positive", *KERNEL_FAULTS):
        case = DIRECTORY / ("kernel-" + name)
        case.mkdir(exist_ok=True)
        model = case / "actual.v"
        model.write_text(source if name == "positive" else kernel_fault(source, name))
        binary = callback_order.compile_native(
            model,
            case,
            "FdcCompletionSourceProbe",
            ROOT / "sim/native/bbc_fdc_completion_source_test.cpp",
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
                    r"(?:COMPLETION_SOURCE_MISMATCH record|SPECIFY_SOURCE_MISMATCH parameter) [0-9]+\n",
                    r.stdout,
                )
                is not None
            )
        )
        if not accepted:
            raise ValueError("Completion source kernel differs: " + str(log))
        checks.append({"name": "kernel-" + name, "accepted": True})
        artifacts.extend([model, binary, log, case / "build.json", case / "build.log"])
        print("Completion kernel", name, "PASS", flush=True)
        if name != "positive":
            continue
        for file, expected in (
            ("original.bin", "COMPLETION_SOURCE_MISMATCH record 33554431\n"),
            ("specify.bin", "SPECIFY_SOURCE_MISMATCH parameter 255\n"),
        ):
            broken = case / ("last-" + file.removesuffix(".bin"))
            broken.mkdir(exist_ok=True)
            fdc_concurrent.stage_fixture_fault(DIRECTORY, broken, ("original.bin", "specify.bin"), file)
            r = subprocess.run(
                [str(binary), str(broken)], capture_output=True, text=True, timeout=120
            )
            log = broken / "run.log"
            log.write_text(r.stdout + r.stderr)
            if r.returncode != 1 or r.stderr or r.stdout != expected:
                raise ValueError("Unchecked final completion record: " + file)
            checks.append({"name": "last-" + file, "accepted": True})
            artifacts.extend([log, broken / "original.bin", broken / "specify.bin"])
    return checks, artifacts


def original(path):
    r = json.loads(path.read_text())
    cases = r.get("cases", [])
    if r.get("passed") is not True or [c.get("name") for c in cases] != NAMES:
        raise ValueError("Incomplete original completion histories")
    for c in cases:
        if (
            type(c.get("count")) is not int
            or c["count"] not in (2, 34, 66)
            or type(c.get("offset")) is not int
            or c["offset"] not in (-1, 0, 1)
            or c.get("observation") not in ("register", "continuation")
            or type(c.get("deadline")) is not int
            or c["deadline"] <= 0
            or c["name"] != f"completion-specify-{c['count']}-{c['offset']}-{c['observation']}"
        ):
            raise ValueError("Invalid completion witness coordinates")
        expected = 296 if c["observation"] == "register" else (300 if c["offset"] == -1 else 298)
        if type(c.get("rows")) is not int or c["rows"] != expected:
            raise ValueError("Incomplete completion observations")
    return cases


def timelines(cases, selected):
    for c in cases:
        rows = [line.split() for line in selected[c["name"]].splitlines()[1:]]
        values = [(int(r[0]), r[1], int(r[2]), int(r[3])) for r in rows]
        deadline = c["deadline"]
        offset = c["offset"]
        if (deadline + offset, "W", 1, 0x17) not in values:
            raise ValueError("Missing completion host deadline")
        programming = [("W", 0, 0x75), ("W", 1, 5), ("W", 1, c["count"])]
        if not any(
            [(r[1], int(r[2]), int(r[3])) for r in rows[i : i + 3]] == programming
            and [int(r[0]) for r in rows[i : i + 3]]
            == list(range(int(rows[i][0]), int(rows[i][0]) + 3))
            and int(rows[i + 2][0]) < deadline - 1
            for i in range(len(rows) - 2)
        ):
            raise ValueError("Missing completion SPECIFY programming")
        coordinates = {(int(r[0]), r[1], int(r[2])) for r in rows}
        wanted = [(2, "R", 3), (3, "R", 2), *((d, "P", 0) for d in (127, 128, 129, 1024, 40000))]
        if any((deadline + d, a, b) not in coordinates for d, a, b in wanted):
            raise ValueError("Missing completion boundary observation")
        if c["observation"] == "register":
            expected = [(deadline + 40001, "W", 0, 0x7D), (deadline + 40002, "W", 1, 6)]
            if (
                not any(values[i : i + 2] == expected for i in range(len(values) - 1))
                or (deadline + 40003, "R", 1) not in coordinates
            ):
                raise ValueError("Missing completion sector-register observation")
        else:
            remaining = 3 if offset < 0 else 2
            for i in range(remaining):
                if (deadline + 40001 + 2 * i, "W", 1, 0x37 + 0x10 * i) not in values or (
                    deadline + 40002 + 2 * i,
                    "P",
                    0,
                ) not in coordinates:
                    raise ValueError("Missing SPECIFY continuation observation")


def qualify():
    current_machine.audit()
    DIRECTORY.mkdir(parents=True, exist_ok=True)
    clocks = [current_machine.DIRECTORY / f"clock{m}/result.json" for m in (1, 3)]
    premises = [
        fdc_byte.DIRECTORY / "result.json",
    ]
    for path in [*clocks, *premises]:
        memory_composition.sealed(path)
    reference = source_root("jsbeeb")
    compiler = ROOT / "tools/bbc/fdc_completion_source.mjs"
    helper = Path(__file__).with_suffix(".mjs")
    files = [
        Path(__file__),
        helper,
        compiler,
        ROOT / "tools/bbc/fdc_byte_source.mjs",
        ROOT / "sim/native/bbc_fdc_completion_source_test.cpp",
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
    if projection.get("passed") is not True or [
        projection.get(k) for k in ("completion_cases", "specify_cases", "registers")
    ] != [33554432, 256, 32]:
        raise ValueError("Incomplete original completion projections")
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
        raise ValueError("Incomplete completion observations or final media")
    checks, artifacts = (
        [],
        [
            DIRECTORY / n
            for n in (
                "source.v",
                "projection.json",
                "original.bin",
                "specify.bin",
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
        raise ValueError("Completion qualification inputs changed")
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
            "scope": "Original completion decisions across all 33554432 ready/count/sector/increment combinations, including Uint8Array underflow, five-bit stop decisions and six-bit increments. SPECIFY dispatch covers every pointer byte. Four actual-controller properties per clock bind ready data-CRC completion to live SPECIFY redispatch, count/sector arithmetic, preserved byte counters and an accepted host parameter write. Three RTL controls must fail formally and in named public observations at both clocks. All 18 cold histories compare 5352 observations per clock and every final raw medium cell. Kernel and final-record/media controls must fail; 35 checks.",
            "limits": "The actual frame requires a valid aligned data CRC, ready loaded lower drive, closed write gate, live SPECIFY, further sectors, idle mechanical engine and no earlier dispatch/index callback and no concurrent host read. The host property covers pointer R5; quiet properties exclude host writes. Ready checking, gate lowering, callback clearing, final completion and other dispatcher calls retain API boundaries. The complete initialized source/controller relation, other live completion commands, scanner/gap aliases, queued timers, formatting and whole CPU/game/board equivalence remain open.",
        },
    )


if __name__ == "__main__":
    qualify()
