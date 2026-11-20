"""Qualify IRQ eligibility and delivery before pending serial-byte bodies."""

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
    fdc_gap,
    fdc_marker,
    fdc_write_gap,
    prepared,
)
from tools.bbc.proofs.video_cursor import hashes, replace_once
from tools.validation.common import ROOT, source_root, write_json

DIRECTORY = ROOT / "build/bbc/fdc-source-pending-irq"
WIDTH = 934
FIELDS = {**fdc_byte.FIELDS, "dataSyncZeros": 2, "mechanicalOp": 4}
EXPORTS = {
    "cell": ("cellBoundary", 1),
    "writeByte": ("isWrite", 1),
    "indexPulse": ("index", 1),
    "present": ("headerPresent", 1),
    "abort": ("serialAbort", 1),
}
PROPERTIES = ("lossEquivalent", "deliveryEquivalent")
FAULTS = {
    "off": ("pending-irq-read-49-126", "deliveryEquivalent"),
    "gating": ("pending-irq-read-1-126", "deliveryEquivalent"),
    "data": ("pending-irq-read-49-126", "deliveryEquivalent"),
    "result": ("pending-irq-read-48-126", "lossEquivalent"),
}
NAMES = [
    f"pending-irq-{kind}-{value}-{offset}"
    for kind in ("read", "write", "run", "marker")
    for value in (1, 48, 49, 112, 113)
    for offset in (126, 128, 130)
]
SUMMARY = "Original prefix: 16777216 reading; 65536 writing; 0 differences\n"


def blocks(source):
    out = []
    for start in re.finditer(r"if\(gapByte\) begin", source):
        depth = 0
        for token in re.finditer(r"\bbegin\b|\bend\b", source[start.start() :]):
            depth += 1 if token[0] == "begin" else -1
            if depth == 0:
                out.append((start.start(), start.start() + token.end()))
                break
        else:
            raise ValueError("Unbalanced prefix block")
    return out


def fault(source, name):
    if name == "off":
        source, n = re.subn(r"(?m)^  assign gapByte = [^;]+;$", "  assign gapByte = 1'b0;", source)
        if n != 1:
            raise ValueError("Changed pending-byte decision")
        return source
    edits = []
    count = 0
    for a, b in blocks(source):
        text = source[a:b]
        if name == "gating":
            text, n = re.subn(r"if\(byteIrqs\)", "if(1'b1)", text)
        elif name == "data":
            text, n = re.subn(r"eventData = io_storageData\[7 : 0\];", "eventData = data;", text)
        elif name == "result":
            text, n = re.subn(r"eventResult = \([^;]+\);", "eventResult = result;", text)
        else:
            raise ValueError("Unknown prefix fault")
        count += n
        edits.append((a, b, text))
    if count != ({"gating": 4, "data": 1, "result": 1}[name]):
        raise ValueError(("Changed prefix target inventory", name, count))
    for a, b, text in reversed(edits):
        source = source[:a] + text + source[b:]
    return source


def indices(body):
    state = body["ports"]["proofState"]["bits"]
    if (
        len(state) != WIDTH
        or len(set(state)) != WIDTH
        or len(body["ports"]["proofNext"]["bits"]) != WIDTH
    ):
        raise ValueError("Incomplete pending-byte controller state")
    fields, used = {}, set()
    for n, w in FIELDS.items():
        b = body["netnames"][n]["bits"]
        if len(b) != w or not set(b).issubset(state) or used.intersection(b):
            raise ValueError("Changed pending-byte field: " + n)
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
    return f"""module PendingBinding({",".join(inputs)},output {",".join(PROPERTIES)});
wire[933:0]following;wire cell,writeByte,indexPulse,present,abort;
FdcStateNext actual({",".join(ports)},.proofNext(following));
wire[7:0]mode={q("mode")},drive={q("driveOut")},command={q("command")};wire[3:0]owner={q("executingOp")},contextOp={q("mechanicalOp")};
wire[7:0]status=(mode&8'h73)|({q("request")}?8'd4:8'd0)|({q("interrupt")}?8'd8:8'd0)|({q("busy")}?8'd128:8'd0);
wire run=writeByte && {q("dataSyncZeros")}==2;
wire calledLoss,dataWritten,aborted,finished,bodyCalled;wire[7:0]followingData,raiseMask,finishResult;
FdcPendingPrefixSource prefix(.status(status),.command(command),.byteValue(io_storageData[7:0]),.oldData({q("data")}),.writingRun(run),.calledLoss(calledLoss),.dataWritten(dataWritten),.aborted(aborted),.finished(finished),.bodyCalled(bodyCalled),.followingData(followingData),.raiseMask(raiseMask),.finishResult(finishResult));
wire quiet={enable} && !reset && !io_warmReset && !io_read && !io_write && !indexPulse && !{q("dispatchPending")} && {q("mechanicalPhase")}==0;
wire gap={q("dataSyncZeros")}==3;
wire marker=!writeByte && {q("dataSyncZeros")}==2 && io_storageData==16'hc7fb;
wire frame=quiet && {q("phase_1")}==8 && {q("readDataPending")} && cell && present && drive[6] && !drive[7] && drive[3] && !drive[5] && {q("position")}==0 && contextOp==owner && owner<=7 && owner!=6 && (gap||run||marker) && drive[0]==run;
assign lossEquivalent=!frame || (abort==aborted && (!finished || (aborted && !bodyCalled && finishResult==8'h0a && {q("phase_1", "following")}==0 && {q("mechanicalPhase", "following")}==0 && !{q("busy", "following")} && {q("ready", "following")} && {q("nmiLevel", "following")} && {q("result", "following")}==({q("result")}|finishResult))));
assign deliveryEquivalent=!frame || finished || (!abort && bodyCalled && {q("data", "following")}==followingData && (raiseMask!=0?({q("nmiLevel", "following")} && {q("request", "following")} && {q("interrupt", "following")} && raiseMask==12):({q("nmiLevel", "following")}=={q("nmiLevel")} && {q("request", "following")}=={q("request")} && {q("interrupt", "following")}=={q("interrupt")})));
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
            f"read_json {cut}; read_verilog {DIRECTORY / 'source.v'} {wrapper}; prep -top PendingBinding -flatten; opt; check -assert; write_json {graph}",
            [cut, DIRECTORY / "source.v", wrapper],
            [graph],
        )
        for prop in PROPERTIES if name == "positive" else (FAULTS[name][1],):
            query = case / prop
            query.mkdir(exist_ok=True)
            slim = query / "graph.json"
            others = " ".join("PendingBinding/w:" + p for p in PROPERTIES if p != prop)
            prepared.prepare(
                query,
                f"read_json {graph}; delete -output {others}; opt_clean -purge; check -assert; write_json {slim}",
                [graph],
                [slim],
            )
            callback_cpu.guard_inputs(
                json.loads(slim.read_text()),
                "PendingBinding",
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
            raise ValueError("Pending IRQ source history differs: " + str(log))
        artifacts.extend([model, binary, log, case / "build.json", case / "build.log"])
        checks.append({"name": "native-" + name, "clock_multiplier": multiplier, "accepted": True})
        print("Pending IRQ", multiplier, name, "PASS", flush=True)
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


KERNEL_TOP = "module PrefixProbe(input[7:0]status,command,byteValue,oldData,input writingRun,output calledLoss,dataWritten,aborted,finished,bodyCalled,output[7:0]followingData,raiseMask,finishResult);FdcPendingPrefixSource prefix(.*);endmodule\n"
KERNEL_FAULTS = {
    "gating": ("calledLoss=irq", "calledLoss=1'b1"),
    "data": ("dataWritten?byteValue:oldData", "oldData"),
    "raised": ("raiseMask=(irq&&ok)?8'd12", "raiseMask=(irq&&ok)?8'd8"),
    "abort": ("aborted=irq&&rawAbort", "aborted=1'b0"),
    "result": ("finishResult=finished?rawResult:8'd0", "finishResult=8'd0"),
    "body": ("bodyCalled=!irq||ok", "bodyCalled=ok"),
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
            "PrefixProbe",
            ROOT / "sim/native/bbc_fdc_pending_irq_source_test.cpp",
        )
        r = subprocess.run(
            [str(binary), str(DIRECTORY)], capture_output=True, text=True, timeout=300
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
                    r"PREFIX_SOURCE_MISMATCH (?:reading|writing) record [0-9]+\n",
                    r.stdout,
                )
                is not None
            )
        )
        if not accepted:
            raise ValueError("Pending IRQ source kernel differs: " + str(log))
        checks.append({"name": "kernel-" + name, "accepted": True})
        artifacts.extend([model, binary, log, case / "build.json", case / "build.log"])
        print("Pending IRQ kernel", name, "PASS", flush=True)
        if name != "positive":
            continue
        for file, expected in (
            ("reading.bin", "PREFIX_SOURCE_MISMATCH reading record 16777215\n"),
            ("writing.bin", "PREFIX_SOURCE_MISMATCH writing record 65535\n"),
        ):
            broken = case / ("last-" + file.removesuffix(".bin"))
            broken.mkdir(exist_ok=True)
            fdc_concurrent.stage_fixture_fault(DIRECTORY, broken, ("reading.bin", "writing.bin"), file)
            r = subprocess.run(
                [str(binary), str(broken)], capture_output=True, text=True, timeout=300
            )
            log = broken / "run.log"
            log.write_text(r.stdout + r.stderr)
            if r.returncode != 1 or r.stderr or r.stdout != expected:
                raise ValueError("Unchecked final gap record: " + file)
            checks.append({"name": "last-" + file, "accepted": True})
            artifacts.extend([log, broken / "reading.bin", broken / "writing.bin"])
    return checks, artifacts


def original(path):
    r = json.loads(path.read_text())
    cases = r.get("cases", [])
    if r.get("passed") is not True or [c.get("name") for c in cases] != NAMES:
        raise ValueError("Incomplete original pending-byte histories")
    for c in cases:
        if (
            c.get("kind") not in ("read", "write", "run", "marker")
            or type(c.get("value")) is not int
            or c["value"] not in (1, 48, 49, 112, 113)
            or type(c.get("offset")) is not int
            or c["offset"] not in (126, 128, 130)
            or type(c.get("rows")) is not int
            or c["rows"] != 39
        ):
            raise ValueError("Malformed pending-byte coordinates")
        if (
            c["name"] != f"pending-irq-{c['kind']}-{c['value']}-{c['offset']}"
            or any(type(c.get(n)) is not int for n in ("entry", "first"))
            or c["entry"] <= 0
            or c["first"] <= c["entry"] + c["offset"] + 1
        ):
            raise ValueError("Invalid original pending-byte deadline")
    return cases


def timelines(cases, selected):
    for c in cases:
        values = [
            (int(r[0]), r[1], int(r[2]), int(r[3]))
            for r in [line.split() for line in selected[c["name"]].splitlines()[1:]]
        ]
        start = c["entry"] + c["offset"] - 2
        wanted = [(start, "W", 0, 0x7A), (start + 1, "W", 1, 23), (start + 2, "W", 1, c["value"])]
        if not any(values[i : i + 3] == wanted for i in range(len(values) - 2)):
            raise ValueError("Missing pending-byte programming deadline")
        coords = {(v[0], v[1], v[2]) for v in values}
        first = c["first"]
        if (
            any((first + d, "P", 0) not in coords for d in (-1, 0, 1, 127, 128, 129, 1024, 2048))
            or (first + 2, "R", 4) not in coords
            or (first + 1025, "R", 1) not in coords
            or (first + 2049, "W", 2, 1) not in values
        ):
            raise ValueError("Missing pending-byte IRQ/data observation")


def qualify():
    current_machine.audit()
    DIRECTORY.mkdir(parents=True, exist_ok=True)
    clocks = [current_machine.DIRECTORY / f"clock{m}/result.json" for m in (1, 3)]
    premises = [
        fdc_byte.DIRECTORY / "result.json",
        fdc_gap.DIRECTORY / "result.json",
        fdc_write_gap.DIRECTORY / "result.json",
        fdc_marker.DIRECTORY / "result.json",
    ]
    for path in [*clocks, *premises]:
        memory_composition.sealed(path)
    reference = source_root("jsbeeb")
    compiler = ROOT / "tools/bbc/fdc_pending_irq_source.mjs"
    helper = Path(__file__).with_suffix(".mjs")
    files = [
        Path(__file__),
        helper,
        compiler,
        ROOT / "tools/bbc/fdc_byte_source.mjs",
        ROOT / "tools/bbc/fdc_ready_source.mjs",
        ROOT / "tools/bbc/fdc_gap_source.mjs",
        ROOT / "tools/bbc/fdc_write_gap_source.mjs",
        ROOT / "sim/native/bbc_fdc_pending_irq_source_test.cpp",
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
                fdc_gap,
                fdc_write_gap,
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
    subprocess.run(["node", str(compiler), str(reference), str(DIRECTORY)], check=True, timeout=600)
    projection = json.loads((DIRECTORY / "projection.json").read_text())
    if projection.get("passed") is not True or [
        projection.get(k) for k in ("reading_cases", "writing_cases", "old_data", "registers")
    ] != [16777216, 65536, 90, 32]:
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
                "projection.json",
                "reading.bin",
                "writing.bin",
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
        raise ValueError("Pending IRQ qualification inputs changed")
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
            "scope": "Original callback IRQ prefixes over all 16777216 status/raw-command/data reading inputs and 65536 status/raw-command writing inputs. Guards enforce write-only R29 effects and exact loss/raise/body-call order. Two all-state properties and four formal/public RTL controls at each clock bind pending read/write gaps, write runs and valid FB marker callbacks. All 60 cold histories compare 2340 observations per rate, explicit data reads and every final medium cell; 33 checks.",
            "limits": "Frames require an aligned supplied FM word, loaded lower drive, retained non-ID context and no simultaneous host/index/earlier callback. Actual marker frames use valid C7/FB; arbitrary bit acquisition and other marker words retain transport/body boundaries. Abort, finish, status-raise and body callees retain explicit APIs. Source tables use old data 90; guarded write-only effects make other old values irrelevant to written data. Other register aliases, queued timers, formatting, weak cells and the complete initialized CPU/game/board relation remain open.",
        },
    )


if __name__ == "__main__":
    qualify()
