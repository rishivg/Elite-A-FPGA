"""Qualify the canonical read-gap register and command sampling order."""

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
    fdc_completion,
    fdc_concurrent,
    fdc_epoch,
    fdc_events,
    fdc_marker,
    fdc_ready,
    prepared,
)
from tools.bbc.proofs.video_cursor import hashes, replace_once
from tools.validation.common import ROOT, source_root, write_json

DIRECTORY = ROOT / "build/bbc/fdc-source-gap"
WIDTH = 934
FIELDS = {
    **fdc_completion.FIELDS,
    "driveInCopy": 8,
    "driveInLatched": 8,
    "dataSyncZeros": 2,
    "mechanicalOp": 4,
}
EXPORTS = {
    **fdc_completion.EXPORTS,
    "cell": ("cellBoundary", 1),
    "present": ("headerPresent", 1),
    "specialStarts": ("writeSpecialStarts", 1),
    "specialTarget": ("writeSpecialTarget", 8),
    "specialValue": ("startParameters_1", 8),
    "commandStarts": ("commandStarts", 1),
    "immediate": ("immediateCompletion", 1),
    "startOp": ("startOp", 4),
}
PROPERTIES = ("counterEquivalent", "hostEquivalent", "sampleEquivalent")
FAULTS = {
    "gap-gate": ("gap-register-0-1", "counterEquivalent"),
    "register": ("gap-register-0-1", "hostEquivalent"),
    "sample": ("gap-command-16-1", "sampleEquivalent"),
}
NAMES = [
    f"gap-register-{value}-{offset}"
    for value in (0, 1, 11, 255)
    for offset in (-1, 0, 1, 127, 128, 129)
] + [f"gap-command-{value}-{offset}" for value in (16, 28, 52) for offset in (0, 1, 127, 128, 129)]
SUMMARY = "Original gap: 65536 decisions; 256 writes; 0 differences\n"


def fault(source, name):
    if name == "gap-gate":
        pattern = r"(?m)^(  assign when_Intel8271_l\d+(?:_\d+)? = )\(dataSyncZeros == 2'b11\);$"
        matches = list(re.finditer(pattern, source))
        if len(matches) != 2:
            raise ValueError("Changed read/write gap-stage inventory")
        found = matches[-1]
        return (
            source[: found.start()]
            + found[1]
            + "(preByteDriveInCopy != 8'h0);"
            + source[found.end() :]
        )
    a, b = {
        "register": ("hostDriveInCopy = startParameters_1;", "hostDriveInCopy = eventDriveInCopy;"),
        "sample": ("hostDriveInCopy = driveIn;", "hostDriveInCopy = eventDriveInCopy;"),
    }[name]
    if source.count(a) != 1:
        raise ValueError("Changed R21 stage target")
    source = source.replace(a, b)
    if name == "register":
        a = "eventDriveInCopy = specialData;"
        if source.count(a) != 1:
            raise ValueError("Changed R21 bank mirror")
        source = source.replace(a, "eventDriveInCopy = driveInCopy;")
    return source


def indices(body):
    state = body["ports"]["proofState"]["bits"]
    if (
        len(state) != WIDTH
        or len(set(state)) != WIDTH
        or len(body["ports"]["proofNext"]["bits"]) != WIDTH
    ):
        raise ValueError("Incomplete read-gap controller state")
    fields, used = {}, set()
    for n, w in FIELDS.items():
        b = body["netnames"][n]["bits"]
        if len(b) != w or not set(b).issubset(state) or used.intersection(b):
            raise ValueError("Changed read-gap field: " + n)
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
    return f"""module GapBinding({",".join(inputs)},output {",".join(PROPERTIES)});
wire[933:0]following;wire writeByte,idByte,indexPulse,indexExpires,headerIrqLoss,cell,present,specialStarts,commandStarts,immediate;wire[7:0]driveIn,specialTarget,specialValue;wire[3:0]startOp;
FdcStateNext actual({",".join(ports)},.proofNext(following));
wire[7:0]gapNext,nextState,writeContext,writeValue,writtenGap,sampledCopy,sampledLatch,statusValue,finishResult;wire stateWritten,writeRun,trap,sourceReady,finished;
FdcGapSource source(.gapByte({q("driveInCopy")}),.contextByte(8'd4),.followingGap(gapNext),.nextState(nextState),.writeContext(writeContext),.writeValue(writeValue),.stateWritten(stateWritten),.writeRun(writeRun),.trap(trap));
FdcGapWriteSource writer(.value(specialValue),.followingGap(writtenGap));
FdcReadySource sample(.pinsByte(driveIn),.driveByte({q("driveOut")}),.oldLatch({q("driveInLatched")}),.followingCopy(sampledCopy),.followingLatch(sampledLatch),.statusValue(statusValue),.finishResult(finishResult),.ready(sourceReady),.finished(finished));
wire[7:0]mode={q("mode")},drive={q("driveOut")};wire[3:0]contextOp={q("mechanicalOp")};
wire quiet={enable} && !reset && !io_warmReset && !io_read && !io_write && !indexPulse && !{q("dispatchPending")} && {q("mechanicalPhase")}==0;
wire gap=quiet && {q("phase_1")}==8 && {q("readDataPending")} && !writeByte && !idByte && cell && present && !drive[0] && mode[5:4]!=3 && {q("dataSyncZeros")}==3 && (contextOp==0 || contextOp==1 || contextOp==4 || contextOp==5 || contextOp==7);
assign counterEquivalent=!gap || ({q("driveInCopy", "following")}==gapNext && {q("dataSyncZeros", "following")}==(stateWritten?0:3) && (!stateWritten || nextState==7) && !writeRun && !trap && {q("phase_1", "following")}==8 && {q("readDataPending", "following")});
wire host={enable} && !reset && !io_warmReset && io_write && specialStarts && specialTarget[5:0]==21;
assign hostEquivalent=!host || {q("driveInCopy", "following")}==writtenGap;
wire sampleFrame={enable} && !reset && !io_warmReset && io_write && commandStarts && !immediate && (startOp==13 || startOp==14) && !(specialStarts && specialTarget[5:0]==21) && !indexPulse && !{q("dispatchPending")} && {q("mechanicalPhase")}==0;
assign sampleEquivalent=!sampleFrame || {q("driveInCopy", "following")}==sampledCopy;
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
            f"read_json {cut}; read_verilog {DIRECTORY / 'source.v'} {wrapper}; prep -top GapBinding -flatten; opt; check -assert; write_json {graph}",
            [cut, DIRECTORY / "source.v", wrapper],
            [graph],
        )
        for prop in PROPERTIES if name == "positive" else (FAULTS[name][1],):
            query = case / prop
            query.mkdir(exist_ok=True)
            slim = query / "graph.json"
            others = " ".join("GapBinding/w:" + p for p in PROPERTIES if p != prop)
            prepared.prepare(
                query,
                f"read_json {graph}; delete -output {others}; opt_clean -purge; check -assert; write_json {slim}",
                [graph],
                [slim],
            )
            callback_cpu.guard_inputs(
                json.loads(slim.read_text()),
                "GapBinding",
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
            raise ValueError("Gap source history differs: " + str(log))
        artifacts.extend([model, binary, log, case / "build.json", case / "build.log"])
        checks.append({"name": "native-" + name, "clock_multiplier": multiplier, "accepted": True})
        print("Gap", multiplier, name, "PASS", flush=True)
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


KERNEL_TOP = """module FdcGapSourceProbe(input[7:0]gapByte,contextByte,value,output[7:0]followingGap,nextState,writeContext,writeValue,writtenGap,output stateWritten,writeRun,trap);
FdcGapSource source(.*);FdcGapWriteSource writer(.value(value),.followingGap(writtenGap));endmodule
"""
KERNEL_FAULTS = {
    "count": ("followingGap-8'd1", "followingGap-8'd2"),
    "wrap": ("if(followingGap)begin", "if(followingGap && gapByte!=0)begin"),
    "read": ("nextState=8'd7;", "nextState=8'd8;"),
    "write": ("writeContext=8'd5;", "writeContext=8'd4;"),
    "trap": ("trap=1'b1;", "trap=1'b0;"),
    "register": ("assign followingGap=value;", "assign followingGap=value^8'd1;"),
}


def kernel_fault(source, name):
    if name not in KERNEL_FAULTS:
        raise ValueError("Unknown gap kernel mutation")
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
            "FdcGapSourceProbe",
            ROOT / "sim/native/bbc_fdc_gap_source_test.cpp",
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
                    r"(?:GAP_SOURCE_MISMATCH record|GAP_WRITE_SOURCE_MISMATCH value) [0-9]+\n",
                    r.stdout,
                )
                is not None
            )
        )
        if not accepted:
            raise ValueError("Gap source kernel differs: " + str(log))
        checks.append({"name": "kernel-" + name, "accepted": True})
        artifacts.extend([model, binary, log, case / "build.json", case / "build.log"])
        print("Gap kernel", name, "PASS", flush=True)
        if name != "positive":
            continue
        for file, expected in (
            ("original.bin", "GAP_SOURCE_MISMATCH record 65535\n"),
            ("writes.bin", "GAP_WRITE_SOURCE_MISMATCH value 255\n"),
        ):
            broken = case / ("last-" + file.removesuffix(".bin"))
            broken.mkdir(exist_ok=True)
            fdc_concurrent.stage_fixture_fault(DIRECTORY, broken, ("original.bin", "writes.bin"), file)
            r = subprocess.run(
                [str(binary), str(broken)], capture_output=True, text=True, timeout=120
            )
            log = broken / "run.log"
            log.write_text(r.stdout + r.stderr)
            if r.returncode != 1 or r.stderr or r.stdout != expected:
                raise ValueError("Unchecked final gap record: " + file)
            checks.append({"name": "last-" + file, "accepted": True})
            artifacts.extend([log, broken / "original.bin", broken / "writes.bin"])
    return checks, artifacts


def original(path):
    r = json.loads(path.read_text())
    cases = r.get("cases", [])
    if r.get("passed") is not True or [c.get("name") for c in cases] != NAMES:
        raise ValueError("Incomplete original read-gap histories")
    for c in cases:
        register = c["name"].startswith("gap-register-")
        values = (0, 1, 11, 255) if register else (16, 28, 52)
        offsets = (-1, 0, 1, 127, 128, 129) if register else (0, 1, 127, 128, 129)
        if (
            type(c.get("value")) is not int
            or c["value"] not in values
            or type(c.get("offset")) is not int
            or c["offset"] not in offsets
            or type(c.get("rows")) is not int
            or c["rows"] != 38
        ):
            raise ValueError("Malformed read-gap coordinates")
        kind = "register" if register else "command"
        if (
            c["name"] != f"gap-{kind}-{c['value']}-{c['offset']}"
            or any(type(c.get(n)) is not int for n in ("gap", "first", "nominal"))
            or c["gap"] <= 0
            or c["nominal"] <= c["gap"]
            or c["first"] <= c["gap"] + c["offset"] + 1
        ):
            raise ValueError("Invalid original read-gap deadline")
    return cases


def timelines(cases, selected):
    for c in cases:
        rows = [line.split() for line in selected[c["name"]].splitlines()[1:]]
        values = [(int(r[0]), r[1], int(r[2]), int(r[3])) for r in rows]
        target = 21 if c["name"].startswith("gap-register-") else 31
        start = c["gap"] + c["offset"] - 2
        wanted = [
            (start, "W", 0, 0x7A),
            (start + 1, "W", 1, target),
            (start + 2, "W", 1, c["value"]),
        ]
        if not any(values[i : i + 3] == wanted for i in range(len(values) - 2)):
            raise ValueError("Missing read-gap programming deadline")
        coordinates = {(v[0], v[1], v[2]) for v in values}
        first = c["first"]
        if (
            any(
                (first + d, "P", 0) not in coordinates
                for d in (-1, 0, 1, 127, 128, 129, 1024, 2048)
            )
            or (first + 1025, "R", 1) not in coordinates
            or (first + 2049, "W", 2, 1) not in values
        ):
            raise ValueError("Missing source-derived read-gap IRQ observation")


def qualify():
    current_machine.audit()
    DIRECTORY.mkdir(parents=True, exist_ok=True)
    clocks = [current_machine.DIRECTORY / f"clock{m}/result.json" for m in (1, 3)]
    premises = [
        fdc_byte.DIRECTORY / "result.json",
        fdc_completion.DIRECTORY / "result.json",
        fdc_ready.DIRECTORY / "result.json",
        fdc_marker.DIRECTORY / "result.json",
    ]
    for path in [*clocks, *premises]:
        memory_composition.sealed(path)
    reference = source_root("jsbeeb")
    compiler = ROOT / "tools/bbc/fdc_gap_source.mjs"
    helper = Path(__file__).with_suffix(".mjs")
    files = [
        Path(__file__),
        helper,
        compiler,
        ROOT / "tools/bbc/fdc_byte_source.mjs",
        ROOT / "tools/bbc/fdc_ready_source.mjs",
        fdc_ready.DIRECTORY / "source.v",
        ROOT / "sim/native/bbc_fdc_gap_source_test.cpp",
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
                fdc_completion,
                fdc_ready,
                fdc_marker,
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
    gap_source = DIRECTORY / "gap.v"
    gap_source.write_bytes((DIRECTORY / "source.v").read_bytes())
    (DIRECTORY / "source.v").write_bytes(
        gap_source.read_bytes() + (fdc_ready.DIRECTORY / "source.v").read_bytes()
    )
    projection = json.loads((DIRECTORY / "projection.json").read_text())
    if projection.get("passed") is not True or [
        projection.get(k) for k in ("gap_cases", "write_cases", "registers")
    ] != [65536, 256, 32]:
        raise ValueError("Incomplete original gap projections")
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
        raise ValueError("Incomplete gap observations or final media")
    checks, artifacts = (
        [],
        [
            DIRECTORY / n
            for n in (
                "source.v",
                "gap.v",
                "projection.json",
                "original.bin",
                "writes.bin",
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
        raise ValueError("Gap qualification inputs changed")
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
            "scope": "Original quiet gap-body decisions over all 65536 byte-counter/context pairs, including zero underflow, read/write API decisions and unsupported-context traps, plus 256 direct R21 writes. Three actual-controller properties at each clock bind read-gap countdown, accepted WRITE SPECIAL and initial command pin sampling. Three RTL controls fail formally and in named public observations. All 39 cold histories compare 1482 observations per clock and every final medium cell; passive probes immediately precede each original-derived first interrupt. Source and media tail controls must fail; 31 checks.",
            "limits": "Actual countdown frames require quiet or partial IRQ eligibility, retained read context, loaded lower drive, cell alignment and no simultaneous host/index/earlier dispatch/mechanical callback. Special-write and command-sampling properties use their decoded acceptance frames. The source byte IRQ prelude, state-change and write-run callees retain boundaries. The inactive read-gap stage is also covered by the sealed marker qualification. Arbitrary bit framing, gap IRQ prefixes, write-gap/write-run aliases, other completion commands, queued timers, formatting and the complete initialized CPU/game/board relation remain open.",
        },
    )


if __name__ == "__main__":
    qualify()
