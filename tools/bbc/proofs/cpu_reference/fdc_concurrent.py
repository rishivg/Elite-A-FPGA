"""Qualify independent mechanical and byte callbacks against cold source histories."""

import json
import re
import shutil
import subprocess
from os import SEEK_END
from pathlib import Path

from tools.bbc.proofs import current_machine, memory_composition
from tools.bbc.proofs.cpu_reference import (
    callback_cpu,
    callback_order,
    fdc_epoch,
    fdc_events,
    fdc_sync,
    prepared,
)
from tools.bbc.proofs.video_cursor import hashes, replace_once
from tools.validation.common import ROOT, source_root, write_json

DIRECTORY = ROOT / "build/bbc/fdc-concurrent"
WIDTH = 934
NAMES = [
    f"overlap-{old}-{new}-{track}-{sector}-{ack}-{offset}"
    for old in (83, 75, 91)
    for new in (105, 83, 75, 91)
    for track in (0, 3)
    for sector in ((0,) if new == 105 else (0, 1))
    for ack in (0, 1)
    for offset in (-1, 0, 1)
]
FAULTS = {
    "stopped-byte": ("overlap-83-105-3-0-1--1", "dispatchEquivalent"),
    "stale-head": ("overlap-83-105-3-0-1--1", "headEquivalent"),
    "stale-clocks": ("overlap-75-105-3-0-1--1", "writeEquivalent"),
    "uncancelled-mechanics": ("overlap-83-105-3-0-0--1", "abortEquivalent"),
    "lost-candidate-irqs": ("overlap-91-91-3-1-1--1", "candidateEquivalent"),
}
PROPERTIES = (
    "dispatchEquivalent",
    "deliveryEquivalent",
    "abortEquivalent",
    "headEquivalent",
    "writeEquivalent",
    "candidateEquivalent",
)
FIELDS = {
    "phase_1": 4,
    "mechanicalPhase": 2,
    "mechanicalDelay": 24,
    "mechanicalOp": 4,
    "dispatchPending": 1,
    "delay": 24,
    "executingOp": 4,
    "command": 8,
    "parameters_0": 8,
    "parameters_1": 8,
    "position": 15,
    "byteCounterLow": 8,
    "byteCounterHigh": 8,
    "readDataPending": 1,
    "dataSyncZeros": 2,
    "mode": 8,
    "data": 8,
    "result": 8,
    "busy": 1,
    "ready": 1,
    "request": 1,
    "interrupt": 1,
    "nmiLevel": 1,
    "nmiGap": 1,
    "driveOut": 8,
    "physicalTrack": 8,
    "currentTrack0": 8,
    "headerSeen": 1,
    "headerRecovering": 1,
    "syncAge": 54,
    "syncCount": 5,
    "syncChecking": 1,
    "mmioData": 8,
    "rotation": 23,
}
EXPORTS = {
    "index": "index",
    "cell": "cellBoundary",
    "target": "retryTarget",
    "candidate": "syncByteEvent",
    "candidateData": "syncCandidateData",
    "scanning": "scanningHeader",
    "abort": "serialAbort",
}
EXPORT_WIDTHS = {"target": 8, "candidateData": 8}


def stage_fixture_fault(source, destination, filenames, corrupted):
    """Flip the last bit of one copied fixture and link the untouched files."""
    for name in filenames:
        original = source / name
        target = destination / name
        target.unlink(missing_ok=True)
        if name != corrupted:
            target.symlink_to(original)
            continue
        shutil.copyfile(original, target)
        with target.open("r+b") as fixture:
            fixture.seek(-1, SEEK_END)
            value = fixture.read(1)[0]
            fixture.seek(-1, SEEK_END)
            fixture.write(bytes([value ^ 1]))


def indices(body):
    state = body["ports"]["proofState"]["bits"]
    if (
        len(state) != WIDTH
        or len(set(state)) != WIDTH
        or len(body["ports"]["proofNext"]["bits"]) != WIDTH
    ):
        raise ValueError("Incomplete concurrent controller state")
    fields, used = {}, set()
    for name, width in FIELDS.items():
        bits = body["netnames"][name]["bits"]
        if len(bits) != width or not set(bits).issubset(state) or used.intersection(bits):
            raise ValueError("Changed concurrent state: " + name)
        fields[name] = [state.index(b) for b in bits]
        used.update(bits)
    return fields


def fault(source, name):
    if name == "stopped-byte":
        pattern = r"mechanicalDelay <= \(stepDelay - 24'h000011\);\n( +)mechanicalPhase <= Mechanical_Seek;"
        matches = list(re.finditer(pattern, source))
        if len(matches) != 1:
            raise ValueError("Changed dispatched seek allocation")
        match = matches[0]
        return replace_once(source, match[0], match[0] + "\n" + match[1] + "phase_1 <= Phase_Idle;")
    if name == "stale-clocks":
        return replace_once(
            source,
            "io_storageWriteData = {(dispatchPending ? dispatchClocks : mmioClocks),mmioData};",
            "io_storageWriteData = {mmioClocks,mmioData};",
        )
    if name == "lost-candidate-irqs":
        statements = re.findall(r"(?m)^  assign candidateIrqs = [^;]+;$", source)
        if len(statements) != 1:
            raise ValueError("Changed candidate IRQ target")
        return replace_once(source, statements[0], "  assign candidateIrqs = 1'b0;")
    if name == "uncancelled-mechanics":
        pattern = r"mechanicalPhase <= Mechanical_Idle;(\n\s+dispatchPending <= 1\x27b0;)"
        # The board reset also clears mechanicalDelay between these assignments.
        text, count = re.subn(pattern, r"/* dropped mechanical cancellation */\1", source)
        if count != 37:
            raise ValueError("Changed mechanical cancellation inventory")
        return text
    if name != "stale-head":
        raise ValueError("Unknown concurrent fault")
    count = 0

    def consume(match):
        nonlocal count
        prefix, lhs, operator, rhs = match.groups()
        if lhs != "mediaPhysicalTrack" and re.search(r"\bmediaPhysicalTrack\b", rhs):
            count += 1
            rhs = re.sub(r"\bmediaPhysicalTrack\b", "physicalTrack", rhs)
        return prefix + lhs + operator + rhs + ";"

    text = re.sub(r"(?m)^(\s*(?:assign )?)(\w+)(\s*=\s*)([^;]+);", consume, source)
    if count != 3:
        raise ValueError("Changed staged-head consumers")
    return text


def contract(body, enable):
    fields = indices(body)

    def q(name, vector="proofState"):
        return "{" + ",".join(f"{vector}[{i}]" for i in reversed(fields[name])) + "}"

    inputs, ports = [], []
    for name, port in body["ports"].items():
        if port["direction"] == "input":
            inputs.append(f"input[{len(port['bits']) - 1}:0]{name}")
            ports.append(f".{name}({name})")
    ports.extend(f".overlap_{n}({n})" for n in EXPORTS)
    return f"""module ConcurrentBinding({",".join(inputs)},output {",".join(PROPERTIES)});
wire[{WIDTH - 1}:0]following;wire index,cell,candidate,scanning,abort;
wire[7:0]target,candidateData;wire[17:0]address;wire[15:0]writeData;wire writeEnable;
FdcStateNext actual({",".join(ports)},.proofNext(following),.io_storageAddress(address),.io_storageWriteData(writeData),.io_storageWrite(writeEnable));
wire base={enable} && !reset && !io_warmReset && !io_read && !io_write && !index;
wire[3:0]phase={q("phase_1")},owner={q("executingOp")};wire[7:0]cmd={q("command")},mode={q("mode")},drive={q("driveOut")};
wire dispatch=base && {q("dispatchPending")} && cmd[5:2]==10  && drive[7:6]==1 && drive[3] && target!=0 && target!={q("currentTrack0")} &&
 phase==8 && owner==4 && {q("delay")}>0 && !{q("readDataPending")};
assign dispatchEquivalent=!dispatch || ({q("phase_1", "following")}==phase && {q("executingOp", "following")}==owner && {q("position", "following")}=={q("position")} &&
 {q("mechanicalPhase", "following")}==1 && {q("mechanicalOp", "following")}==10 && {q("readDataPending", "following")}=={q("readDataPending")});
wire waiting=base && !{q("dispatchPending")} && ({q("mechanicalPhase")}==1 || {q("mechanicalPhase")}==2) && {q("mechanicalDelay")}>0;
wire readByte=waiting && phase==8 && owner==4 && {q("delay")}==0 && !{q("readDataPending")} && mode[5:4]==3 && mode[0] && cmd[5:2]>1 && io_storageData[15:8]==255;
wire readComplete={q("byteCounterLow")}==1 && {q("byteCounterHigh")}==0;
assign deliveryEquivalent=!readByte || {q("request")} || ({q("data", "following")}==io_storageData[7:0] && {q("nmiLevel", "following")} && {q("request", "following")} &&
 {q("mechanicalPhase", "following")}=={q("mechanicalPhase")} && {q("mechanicalDelay", "following")}=={q("mechanicalDelay")}-24'd1 && {q("delay", "following")}==(readComplete?4095:2047));
assign abortEquivalent=!readByte || !{q("request")} || (abort && {q("phase_1", "following")}==0 && {q("mechanicalPhase", "following")}==0 &&
 !{q("busy", "following")} && {q("ready", "following")} && {q("result", "following")}==({q("result")} | 8'h0a) && {q("nmiLevel", "following")});
wire[7:0]physical={q("physicalTrack")};
wire headDomain=base && {q("dispatchPending")} && cmd[5:2]==10  && drive[7:6]==1 && !drive[5] && drive[3] && target!=0 && target!={q("currentTrack0")} && physical<79 && cell && {q("rotation")}<6400000;
wire[7:0]stepped=target>{q("currentTrack0")}?physical+8'd1:(physical==0?8'd0:physical-8'd1);
assign headEquivalent=!headDomain || address==stepped*18'd3125+({q("rotation")}/23'd2048==3124?18'd0:({q("rotation")}/23'd2048)+18'd1);
wire writeDomain=base && {q("dispatchPending")} && cmd[5:2]==10  && drive[7:6]==1 && drive[3] && !drive[5] && drive[0] && physical<79 && target!=0 && target!={q("currentTrack0")} &&
 cell && phase==8 && (owner==2 || owner==3) && {q("delay")}>0 && mode[0];
wire pendingAbort={q("readDataPending")} && ({q("dataSyncZeros")}==3 || {q("dataSyncZeros")}==2) && mode[5:4]==3 && {q("request")};
assign writeEquivalent=!writeDomain || (abort==pendingAbort && writeEnable && writeData==(pendingAbort?16'hffff:{{8'h40,{q("mmioData")}}}));
wire candidateDomain=base && !{q("dispatchPending")} && ({q("mechanicalPhase")}==0 || {q("mechanicalDelay")}>0) && scanning && candidate && mode[5:4]==3 && mode[0] && cmd[5:2]>1 && !{q("request")} && !{q("nmiGap")} &&
 {q("syncAge")}<=54'h20000000000000 && {q("syncCount")}<16 && (!{q("syncChecking")} || {q("syncAge")}==0);
assign candidateEquivalent=!candidateDomain || (!abort && {q("data", "following")}==candidateData && {q("nmiLevel", "following")} && {q("request", "following")} && {q("interrupt", "following")});
endmodule
"""


def formal(multiplier, parent, top):
    controller, extra = callback_order.fdc_instance(parent.read_text(), top)
    checks, artifacts = [], []
    for name in ("positive", *FAULTS):
        case = DIRECTORY / f"formal-{name}{multiplier}"
        case.mkdir(exist_ok=True)
        text = controller if name == "positive" else fault(controller, name)
        header = ",".join(f"output[{EXPORT_WIDTHS.get(n, 1) - 1}:0]overlap_{n}" for n in EXPORTS)
        text = replace_once(text, "module Intel8271 (", "module Intel8271 (" + header + ",")
        text = replace_once(
            text,
            "endmodule",
            "\n" + "".join(f"assign overlap_{n}={s};\n" for n, s in EXPORTS.items()) + "endmodule",
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
            f"read_json {cut}; read_verilog {wrapper}; prep -top ConcurrentBinding -flatten; opt; check -assert; write_json {graph}",
            [cut, wrapper],
            [graph],
        )
        callback_cpu.guard_inputs(
            json.loads(graph.read_text()),
            "ConcurrentBinding",
            {n: len(p["bits"]) for n, p in body["ports"].items() if p["direction"] == "input"},
        )
        for prop in PROPERTIES if name == "positive" else (FAULTS[name][1],):
            query = case / prop
            query.mkdir(exist_ok=True)
            slim = query / "graph.json"
            others = " ".join("ConcurrentBinding/w:" + n for n in PROPERTIES if n != prop)
            prepared.prepare(
                query,
                f"read_json {graph}; delete -output {others}; opt_clean -purge; check -assert; write_json {slim}",
                [graph],
                [slim],
            )
            callback_cpu.guard_inputs(
                json.loads(slim.read_text()),
                "ConcurrentBinding",
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
                {"name": name, "property": prop, "clock_multiplier": multiplier, "accepted": True}
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


def original(path):
    report = json.loads(path.read_text())
    cases = report.get("cases", [])
    if report.get("passed") is not True or [c.get("name") for c in cases] != NAMES:
        raise ValueError("Incomplete source overlap history inventory")
    for c in cases:
        expected = f"overlap-{c.get('old')}-{c.get('next')}-{c.get('track')}-{c.get('sector')}-{int(c.get('ack', False))}-{c.get('offset')}"
        if (
            any(type(c.get(k)) is not int for k in ("old", "next", "track", "sector", "offset"))
            or type(c.get("ack")) is not bool
            or expected != c["name"]
            or type(c.get("rows")) is not int
            or c["rows"] <= 0
            or type(c.get("first")) is not int
            or not 850000 < c["first"] < 1500000
            or c.get("deadline") != c["first"] + 128
        ):
            raise ValueError("Changed source overlap witness")
    return cases


def media_mismatch(trial, name):
    match = re.fullmatch(
        r"FDC_MEDIA_MISMATCH " + re.escape(name) + r" cell ([0-9]+) value ([0-9]+)/([0-9]+)\n",
        trial.stdout,
    )
    return (
        trial.returncode == 1
        and not trial.stderr
        and match is not None
        and 0 <= int(match[1]) < 250000
        and 0 <= int(match[2]) <= 65535
        and 0 <= int(match[3]) <= 65535
        and int(match[2]) != int(match[3])
    )


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
                or media_mismatch(trial, FAULTS[name][0])
            )
        )
        if not accepted:
            raise ValueError("Concurrent source history differs: " + str(log))
        artifacts.extend([model, binary, log, case / "build.json", case / "build.log"])
        checks.append({"name": "native-" + name, "clock_multiplier": multiplier, "accepted": True})
        print("Concurrent", multiplier, name, "PASS", flush=True)
        if name == "positive":
            broken = case / "last-media"
            broken.mkdir(exist_ok=True)
            stage_fixture_fault(
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
            if not media_mismatch(control, NAMES[-1]) or "cell 249999 " not in control.stdout:
                raise ValueError("Final media leaf was not checked")
            checks.append(
                {"name": "last-media-cell", "clock_multiplier": multiplier, "accepted": True}
            )
            artifacts.extend([control_log, *[broken / f"media-{i}.bin" for i in range(count)]])
    return checks, artifacts


def sync_premise(report):
    expected = {
        (n, None, None)
        for n in (
            "kernel-positive",
            "kernel-wrong-byte",
            "kernel-early-plateau",
            "last-oracle-record",
        )
    }
    for multiplier in (1, 3):
        expected.update(
            ("positive", p, multiplier)
            for p in ("prefixEquivalent", "ageBound", "checkingBound", "countBound")
        )
        expected.add(("wrong-delimited-byte", "prefixEquivalent", multiplier))
        expected.update(
            (n, None, multiplier) for n in ("native-positive", "native-wrong-delimited-byte")
        )
    checks = report.get("checks", [])
    if (
        report.get("passed") is not True
        or report.get("register_bits") != WIDTH
        or len(checks) != 18
        or any(c.get("accepted") is not True for c in checks)
        or {(c.get("name"), c.get("property"), c.get("clock_multiplier")) for c in checks}
        != expected
    ):
        raise ValueError("Incomplete original sync-prefix/counter-frame premise")


def qualify():
    current_machine.audit()
    DIRECTORY.mkdir(parents=True, exist_ok=True)
    clocks = [current_machine.DIRECTORY / f"clock{m}/result.json" for m in (1, 3)]
    for p in clocks:
        memory_composition.sealed(p)
    sync_premise(memory_composition.sealed(fdc_sync.DIRECTORY / "result.json"))
    reference = source_root("jsbeeb")
    helper = Path(__file__).with_suffix(".mjs")
    files = [
        Path(__file__),
        helper,
        ROOT / "sim/native/bbc_fdc_branch_test.cpp",
        ROOT / "sim/native/bbc_disc_payload.h",
        ROOT / "sim/native/bbc_fm_media.h",
        ROOT / "tools/bbc/reference_drive.mjs",
        ROOT / "build/bbc/disc.ssd",
        *[ROOT / n for n in current_machine.generation_inputs()],
        current_machine.DIRECTORY / "generation.json",
        *clocks,
        fdc_sync.DIRECTORY / "result.json",
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
                fdc_sync,
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
        timeout=180,
    )
    cases = original(DIRECTORY / "original.json")
    table = DIRECTORY / "original.tsv"
    selected = fdc_events.fixtures(table.read_text(), NAMES)
    media = [DIRECTORY / f"media-{i}.bin" for i in range(len(NAMES))]
    if any(p.stat().st_size != 500000 for p in media) or any(
        int(selected[c["name"]].splitlines()[0].split()[-1]) != c["rows"] for c in cases
    ):
        raise ValueError("Incomplete overlap observations or media")
    checks, artifacts = [], [table, DIRECTORY / "original.json", *media]
    for m, p, t in current_machine.parents():
        for work in (
            lambda: formal(m, p, t),
            lambda: native(m, p, t, sum(c["rows"] for c in cases)),
        ):
            c, a = work()
            checks.extend(c)
            artifacts.extend(a)
    current_machine.audit()
    if before != hashes(files):
        raise ValueError("Concurrent inputs changed")
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
            "full_equivalence": False,
            "scope": "Six all-state actual-controller properties cover dispatched nonzero seeks preserving a waiting read byte program, read delivery and late-DMA cancellation while mechanics wait, staged head movement, dispatch clock words, and candidate IRQ retirement in the stated local domains. Original public execution covers 252 overlapping read/write/read-ID and replacement commands, target tracks zero/three, acknowledged/pending bytes, and three deadline offsets at both clock rates. Every final raw FM medium cell is checked. Five actual RTL faults need formal and initialized native witnesses; the final cell of the final branch must detect a fixture fault. The source-sync prefix report is sealed as a separate premise.",
            "limits": "These local properties and finite cold histories do not establish a complete initialized source/controller relation, whole-scanner continuation, arbitrary byte framing, formatting, weak-cell behavior or whole-machine/game equivalence.",
        },
    )


if __name__ == "__main__":
    qualify()
