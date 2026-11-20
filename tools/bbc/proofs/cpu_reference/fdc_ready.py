"""Qualify canonical READY writes and completion readiness checks."""

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
    prepared,
)
from tools.bbc.proofs.video_cursor import hashes, replace_once
from tools.validation.common import ROOT, source_root, write_json

DIRECTORY = ROOT / "build/bbc/fdc-source-ready"
WIDTH = 934
FIELDS = {**fdc_completion.FIELDS, "driveInLatched": 8, "driveInCopy": 8}
EXPORTS = {
    **fdc_completion.EXPORTS,
    "bankWrite": ("specialWrite", 1),
    "bankAddress": ("specialAddress", 5),
    "bankValue": ("specialData", 8),
}
PROPERTIES = ("completionEquivalent", "writeEquivalent", "latchInvariant")
FAULTS = {
    "ready-check": ("completion-ready-0", "completionEquivalent"),
    "register": ("completion-ready-0", "writeEquivalent"),
    "latch": ("completion-ready-0", "latchInvariant"),
}
NAMES = [f"completion-ready-{value}" for value in (0, 129, 4, 68)] + [
    f"completion-ready-{value}-{offset}" for value in (0, 129, 4, 68) for offset in (-1, 0, 1)
]
SUMMARY = "Original READY: 16777216 decisions; 256 latch writes; 0 differences\n"


def fault(source, name):
    if name == "ready-check":
        pattern = r"(?m)^(  assign when_Intel8271_l\d+(?:_\d+)? = )\(\(! driveIn\[2\]\) \|\| \(! latchedReady\)\);$"
        source, n = re.subn(pattern, lambda x: x[1] + "1'b0;", source)
        if n != 3:
            raise ValueError("Changed sector ready checks")
        return source
    targets = [("latchedReady <= specialData[2];", "latchedReady <= latchedReady;")]
    if name == "register":
        targets.append(("driveInLatched <= specialData;", "driveInLatched <= driveInLatched;"))
    elif name != "latch":
        raise ValueError("Unknown READY mutation")
    for a, b in targets:
        if source.count(a) != 1:
            raise ValueError("Changed latch mirror")
        source = source.replace(a, b)
    return source


def indices(body):
    state = body["ports"]["proofState"]["bits"]
    if (
        len(state) != WIDTH
        or len(set(state)) != WIDTH
        or len(body["ports"]["proofNext"]["bits"]) != WIDTH
    ):
        raise ValueError("Incomplete READY controller state")
    fields, used = {}, set()
    for n, w in FIELDS.items():
        b = body["netnames"][n]["bits"]
        if len(b) != w or not set(b).issubset(state) or used.intersection(b):
            raise ValueError("Changed READY field: " + n)
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
    return f"""module ReadyBinding({",".join(inputs)},output {",".join(PROPERTIES)});
wire[933:0]following;wire writeByte,idByte,indexPulse,indexExpires,headerIrqLoss,bankWrite;wire[4:0]bankAddress;wire[7:0]driveIn,bankValue;
FdcStateNext actual({",".join(ports)},.proofNext(following));
wire[7:0]sampledCopy,sampledLatch,statusValue,finishResult,writtenLatch;wire sourceReady,finished;
FdcReadySource source(.pinsByte(driveIn),.driveByte({q("driveOut")}),.oldLatch({q("driveInLatched")}),.followingCopy(sampledCopy),.followingLatch(sampledLatch),.statusValue(statusValue),.finishResult(finishResult),.ready(sourceReady),.finished(finished));
FdcReadyWriteSource writer(.value(bankValue),.followingLatch(writtenLatch));
wire[7:0]oldLatch={q("driveInLatched")},nextLatch={q("driveInLatched", "following")},drive={q("driveOut")};
wire oldInvariant={q("latchedReady")}==oldLatch[2],nextInvariant={q("latchedReady", "following")}==nextLatch[2];
assign latchInvariant=reset || !oldInvariant || nextInvariant;
wire writeFrame={enable} && !reset && bankWrite && bankAddress==27;
assign writeEquivalent=!writeFrame || (nextLatch==writtenLatch && {q("latchedReady", "following")}==writtenLatch[2]);
wire frame={enable} && !reset && !io_warmReset && !io_read && !io_write && !{q("dispatchPending")} && {q("mechanicalPhase")}==0 && {q("phase_1")}==9 && {q("delay")}==0 && !indexPulse && !indexExpires && !headerIrqLoss && !writeByte && !idByte && !drive[0] && drive[3] && drive[7:6]==1 && io_storageData[15:8]==8'hff && {q("readCrc")}=={{{q("readCrcHigh")},io_storageData[7:0]}} && oldInvariant && !sourceReady;
assign completionEquivalent=!frame || (finished && finishResult==16 && {q("driveInCopy", "following")}==sampledCopy && nextLatch==sampledLatch && {q("phase_1", "following")}==0 && {q("mechanicalPhase", "following")}==0 && !{q("busy", "following")} && {q("ready", "following")} && {q("interrupt", "following")} && {q("nmiLevel", "following")} && {q("result", "following")}==({q("result")}|finishResult) && {q("parameters_2", "following")}=={q("parameters_2")} && {q("parameters_1", "following")}=={q("parameters_1")});
endmodule
"""


def reset_inventory(body):
    selected = set()
    for name, width in (("driveInLatched", 8), ("latchedReady", 1)):
        bits = body["netnames"][name]["bits"]
        if len(bits) != width or selected.intersection(bits):
            raise ValueError("Changed READY reset fields")
        selected.update(bits)
    found = set()
    for cell in body["cells"].values():
        q = cell.get("connections", {}).get("Q", [])
        overlap = selected.intersection(q)
        if not overlap:
            continue
        params = cell["parameters"]
        if (
            cell["type"] != "$adff"
            or cell["connections"].get("ARST") != body["ports"]["reset"]["bits"]
            or int(params.get("ARST_POLARITY", "0"), 2) != 1
            or cell["connections"].get("CLK") != body["ports"]["clk"]["bits"]
            or int(params.get("CLK_POLARITY", "0"), 2) != 1
        ):
            raise ValueError("Changed READY asynchronous reset")
        value = params["ARST_VALUE"]
        if (
            len(value) != len(q)
            or any(value[-1 - q.index(bit)] != "0" for bit in overlap)
            or found.intersection(overlap)
        ):
            raise ValueError("Nonzero or duplicate READY reset")
        found.update(overlap)
    if found != selected:
        raise ValueError("Incomplete READY reset inventory")
    return len(found)


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
        raw_data = json.loads(raw.read_text())
        reset_inventory(raw_data["modules"]["Intel8271"])
        data = callback_cpu.cut_fdc(raw_data)
        body = data["modules"]["FdcStateNext"]
        write_json(cut, data)
        wrapper.write_text(contract(body, extra[0] if extra else "1'b1"))
        lower = case / "lower"
        lower.mkdir(exist_ok=True)
        prepared.prepare(
            lower,
            f"read_json {cut}; read_verilog {DIRECTORY / 'source.v'} {wrapper}; prep -top ReadyBinding -flatten; opt; check -assert; write_json {graph}",
            [cut, DIRECTORY / "source.v", wrapper],
            [graph],
        )
        for prop in PROPERTIES if name == "positive" else (FAULTS[name][1],):
            query = case / prop
            query.mkdir(exist_ok=True)
            slim = query / "graph.json"
            others = " ".join("ReadyBinding/w:" + p for p in PROPERTIES if p != prop)
            prepared.prepare(
                query,
                f"read_json {graph}; delete -output {others}; opt_clean -purge; check -assert; write_json {slim}",
                [graph],
                [slim],
            )
            callback_cpu.guard_inputs(
                json.loads(slim.read_text()),
                "ReadyBinding",
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
            raise ValueError("READY source history differs: " + str(log))
        artifacts.extend([model, binary, log, case / "build.json", case / "build.log"])
        checks.append({"name": "native-" + name, "clock_multiplier": multiplier, "accepted": True})
        print("READY", multiplier, name, "PASS", flush=True)
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


KERNEL_TOP = """module FdcReadySourceProbe(input[7:0]pinsByte,driveByte,oldLatch,value,output[7:0]followingCopy,followingLatch,statusValue,finishResult,writtenLatch,output ready,finished);
FdcReadySource source(.*);FdcReadyWriteSource writer(.value(value),.followingLatch(writtenLatch));endmodule
"""
KERNEL_FAULTS = {
    "latch": ("followingLatch=oldLatch;", "followingLatch=0;"),
    "mask": ("8'd187", "8'd255"),
    "selection": ("driveByte & 8'd128", "driveByte & 8'd64"),
    "result": ("finishResult=8'd16;", "finishResult=8'd0;"),
    "copy": ("followingCopy=driveIn;", "followingCopy=0;"),
    "write": ("assign followingLatch=value;", "assign followingLatch=value^8'd1;"),
}


def kernel_fault(source, name):
    if name not in KERNEL_FAULTS:
        raise ValueError("Unknown READY kernel mutation")
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
            "FdcReadySourceProbe",
            ROOT / "sim/native/bbc_fdc_ready_source_test.cpp",
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
                    r"(?:READY_SOURCE_MISMATCH record|READY_WRITE_SOURCE_MISMATCH value) [0-9]+\n",
                    r.stdout,
                )
                is not None
            )
        )
        if not accepted:
            raise ValueError("Ready source kernel differs: " + str(log))
        checks.append({"name": "kernel-" + name, "accepted": True})
        artifacts.extend([model, binary, log, case / "build.json", case / "build.log"])
        print("Ready kernel", name, "PASS", flush=True)
        if name != "positive":
            continue
        for file, expected in (
            ("original.bin", "READY_SOURCE_MISMATCH record 16777215\n"),
            ("writes.bin", "READY_WRITE_SOURCE_MISMATCH value 255\n"),
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
                raise ValueError("Unchecked final READY record: " + file)
            checks.append({"name": "last-" + file, "accepted": True})
            artifacts.extend([log, broken / "original.bin", broken / "writes.bin"])
    return checks, artifacts


def original(path):
    r = json.loads(path.read_text())
    cases = r.get("cases", [])
    if r.get("passed") is not True or [c.get("name") for c in cases] != NAMES:
        raise ValueError("Incomplete original READY histories")
    for c in cases:
        if (
            c.get("kind") not in ("early", "timed")
            or type(c.get("value")) is not int
            or c["value"] not in (0, 129, 4, 68)
            or type(c.get("deadline")) is not int
            or c["deadline"] <= 0
        ):
            raise ValueError("Invalid READY witness coordinates")
        timed = c["kind"] == "timed"
        if timed and (type(c.get("offset")) is not int or c["offset"] not in (-1, 0, 1)):
            raise ValueError("Changed READY programming deadline")
        name = f"completion-ready-{c['value']}" + (f"-{c['offset']}" if timed else "")
        if (
            c["name"] != name
            or (not timed and "offset" in c)
            or type(c.get("rows")) is not int
            or c["rows"] != (287 if timed else 288)
        ):
            raise ValueError("Incomplete READY observations")
    return cases


def timelines(cases, selected):
    for c in cases:
        rows = [line.split() for line in selected[c["name"]].splitlines()[1:]]
        values = [(int(r[0]), r[1], int(r[2]), int(r[3])) for r in rows]
        timed = c["kind"] == "timed"
        d = c["deadline"]
        offset = c.get("offset", -1)
        start = d + offset - 2 if timed else d - 254
        expected = [(start, "W", 0, 0x7A), (start + 1, "W", 1, 27), (start + 2, "W", 1, c["value"])]
        if not any(values[i : i + 3] == expected for i in range(len(values) - 2)):
            raise ValueError("Missing READY programming deadline")
        expected_result = 16 if (not timed or offset < 0) and not (c["value"] & 4) else 0
        if (d + (2 if timed else 1), "R", 1, expected_result) not in values or (
            d + 129,
            "W",
            2,
            1,
        ) not in values:
            raise ValueError("Missing READY completion or reset observation")
        coordinates = {(v[0], v[1], v[2]) for v in values}
        if (d + 128, "P", 0) not in coordinates or (
            not timed and (d - 1, "P", 0) not in coordinates
        ):
            raise ValueError("Missing READY boundary observation")


def qualify():
    current_machine.audit()
    DIRECTORY.mkdir(parents=True, exist_ok=True)
    clocks = [current_machine.DIRECTORY / f"clock{m}/result.json" for m in (1, 3)]
    premises = [
        fdc_byte.DIRECTORY / "result.json",
        fdc_completion.DIRECTORY / "result.json",
    ]
    for path in [*clocks, *premises]:
        memory_composition.sealed(path)
    reference = source_root("jsbeeb")
    compiler = ROOT / "tools/bbc/fdc_ready_source.mjs"
    helper = Path(__file__).with_suffix(".mjs")
    files = [
        Path(__file__),
        helper,
        compiler,
        ROOT / "tools/bbc/fdc_byte_source.mjs",
        ROOT / "sim/native/bbc_fdc_ready_source_test.cpp",
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
        projection.get(k) for k in ("ready_cases", "write_cases", "registers")
    ] != [16777216, 256, 32]:
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
        raise ValueError("READY qualification inputs changed")
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
            "scope": "Original drive-status sampling and READY decisions over all 16777216 pin/drive/latch combinations, plus 256 direct typed R27 writes. Actual-controller properties bind canonical register writes and not-ready sector completion. An all-input non-reset transition property preserves the R27/READY alias relation; every actual asynchronous reset bit for these fields is separately audited at zero. Three RTL faults fail formally and in named public observations at both clocks. All 16 cold histories compare 4596 observations per clock and every final raw medium cell. Source and media tail controls must fail; 31 checks.",
            "limits": "Completion frames require aligned valid data CRC, a loaded lower drive, idle mechanical engine, consistent READY alias and no simultaneous host/index/earlier dispatch callback. Physical drive-pin correspondence and finish effects remain API boundaries. Alias closure and reset inventory are actual RTL facts; the full initialized original/controller relation is not proved. Other completion commands, live gap aliases, queued timers, formatting, weak cells and whole CPU/game/board equivalence remain open.",
        },
    )


if __name__ == "__main__":
    qualify()
