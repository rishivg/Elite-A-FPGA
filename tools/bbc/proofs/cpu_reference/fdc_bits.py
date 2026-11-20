"""Exhaust original bit kernels and bind CRC updates to actual controller state."""

import json
import re
import shutil
import subprocess
from pathlib import Path

from tools.bbc.proofs import current_machine, memory_composition
from tools.bbc.proofs.cpu_reference import (
    callback_cpu,
    callback_order,
    fdc_epoch,
    fdc_events,
    fdc_order,
    prepared,
)
from tools.bbc.proofs.video_cursor import hashes, replace_once
from tools.validation.common import ROOT, source_root, write_json

DIRECTORY = ROOT / "build/bbc/fdc-source-bits"
FIELDS = {
    "phase_1": 4,
    "mechanicalPhase": 2,
    "mechanicalDelay": 24,
    "dispatchPending": 1,
    "rotation": 23,
    "headerSeen": 1,
    "headerRecovering": 1,
    "headerCrc": 16,
    "readCrc": 16,
    "writeCrc": 16,
    "mode": 8,
    "request": 1,
    "data": 8,
    "position": 15,
}
PROPERTIES = ("headerEquivalent", "readEquivalent", "writeEquivalent")
SUMMARY = "Original bit kernels: 16777216 CRC, 65536 FM, 65536x4 extraction cases; 0 differences\n"


def crc_fault(source):
    count = 0

    def change(match):
        nonlocal count
        if match[1] == "16'hffff":
            return match[0]
        count += 1
        return "headerCrc <= (" + match[1] + ") ^ 16'h0001;"

    changed = re.sub(r"headerCrc <= ([^;]+);", change, source)
    if count != 5:
        raise ValueError("Changed actual header CRC update inventory")
    return changed


def contract(body, enable):
    state = body["ports"]["proofState"]["bits"]
    if (
        len(state) != 934
        or len(set(state)) != 934
        or len(body["ports"]["proofNext"]["bits"]) != 934
    ):
        raise ValueError("Incomplete bit-kernel controller state")
    fields, used = {}, set()
    for name, width in FIELDS.items():
        bits = body["netnames"][name]["bits"]
        if len(bits) != width or not set(bits).issubset(state) or used.intersection(bits):
            raise ValueError("Changed CRC state: " + name)
        fields[name] = [state.index(bit) for bit in bits]
        used.update(bits)

    def q(name, vector="proofState"):
        return "{" + ",".join(f"{vector}[{i}]" for i in reversed(fields[name])) + "}"

    wires, ports = [], []
    for port, signal in {
        "present": "headerPresent",
        "clock": "anyHeaderClock",
        "indexPulse": "index",
        "arriving": "arrivingByte",
        "writing": "isWrite",
        "id": "isId",
        "scan": "isScan",
        "valid": "validDataClocks",
        "aborting": "abortMedia",
    }.items():
        bits = body["netnames"][signal]["bits"]
        body["ports"]["bits_" + port] = {"direction": "output", "bits": bits}
        wires.append(f"wire[{len(bits) - 1}:0] {port};")
        ports.append(f".bits_{port}({port})")
    inputs = []
    for name, port in body["ports"].items():
        if port["direction"] == "input":
            inputs.append(f"input[{len(port['bits']) - 1}:0] {name}")
            ports.append(f".{name}({name})")
    return f"""module CrcActual({",".join(inputs)},output {",".join(PROPERTIES)});
{"".join(wires)}
wire[933:0] following;
FdcStateNext actual({",".join(ports)},.proofNext(following));
wire[31:0]headerValue,readValue,writeValue;
FdcCrcSource h(.arg0({q("headerCrc")}),.arg1(io_storageData[7:0]),.value(headerValue));
FdcCrcSource r(.arg0({q("readCrc")}),.arg1(io_storageData[7:0]),.value(readValue));
FdcCrcSource w(.arg0({q("writeCrc")}),.arg1({q("data")}),.value(writeValue));
wire common={enable} && !reset && !io_read && !io_write && !io_warmReset && !indexPulse && !{q("dispatchPending")} && ({q("mechanicalPhase")}==0 || {q("mechanicalDelay")}>0);
wire[7:0]md={q("mode")};
wire headerService=md[5:4]!=3 || (md[0] && !scan && !{q("request")});
wire hDomain=common && headerService && {q("phase_1")}==7 && {q("headerSeen")} && !{q("headerRecovering")} && present && io_storageData[15:8]==255 && {q("rotation")}==clock+32'd2047;
wire service=md[5:4]!=3 || (md[0] && !scan);
wire rDomain=common && arriving && !writing && !id && valid && service;
wire wDomain=common && arriving && writing && !aborting && service && {q("position")}!=0;
assign headerEquivalent=!hDomain || {q("headerCrc", "following")}==headerValue;
assign readEquivalent=!rDomain || {q("readCrc", "following")}==readValue;
assign writeEquivalent=!wDomain || {q("writeCrc", "following")}==writeValue;
endmodule
"""


def formal(multiplier, parent, top):
    controller, extra = callback_order.fdc_instance(parent.read_text(), top)
    checks, artifacts = [], []
    for name, text in (("positive", controller), ("bad-header-crc", crc_fault(controller))):
        case = DIRECTORY / f"actual-{name}{multiplier}"
        case.mkdir(exist_ok=True)
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
        wrapper.write_text(contract(body, extra[0] if extra else "1'b1"))
        write_json(cut, data)
        lower = case / "lower"
        lower.mkdir(exist_ok=True)
        prepared.prepare(
            lower,
            f"read_json {cut}; read_verilog {DIRECTORY / 'source.v'} {wrapper}; prep -top CrcActual -flatten; opt; check -assert; write_json {graph}",
            [cut, DIRECTORY / "source.v", wrapper],
            [graph],
        )
        callback_cpu.guard_inputs(
            json.loads(graph.read_text()),
            "CrcActual",
            {n: len(p["bits"]) for n, p in body["ports"].items() if p["direction"] == "input"},
        )
        for prop in PROPERTIES if name == "positive" else PROPERTIES[:1]:
            query = case / prop
            query.mkdir(exist_ok=True)
            artifacts.extend(
                callback_cpu.prove(
                    query,
                    f"read_json {graph}",
                    prop,
                    name == "positive",
                    horizon=0,
                    show="-show hDomain -show rDomain -show wDomain",
                )
            )
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
                case / "prepare.log",
                case / "prepared.json",
                lower / "prepare.log",
                lower / "prepared.json",
            ]
        )
    return checks, artifacts


def kernels():
    source = (DIRECTORY / "source.v").read_text()
    wrapper = """module FdcBitProbe(input[15:0]crc,input[7:0]data,clocks,input[31:0]bits,
output[31:0]crcValue,pulses,extracted);
FdcCrcSource c(.arg0(crc),.arg1(data),.value(crcValue));
FdcFmSource f(.arg0(clocks),.arg1(data),.value(pulses));
FdcExtractSource e(.arg0(bits),.value(extracted));
endmodule
"""
    checks, artifacts = [], []
    for name, module, prefix in (
        ("positive", None, None),
        ("bad-crc", "FdcCrcSource", "CRC_MISMATCH"),
        ("bad-fm", "FdcFmSource", "FM_MISMATCH"),
        ("bad-extract", "FdcExtractSource", "EXTRACT_MISMATCH"),
    ):
        case = DIRECTORY / name
        case.mkdir(exist_ok=True)
        text = source
        if module:
            start = text.index("module " + module + "(")
            end = text.index("endmodule", start)
            part = text[start:end]
            match = re.findall(r"(?m)^value = ([^;]+);$", part)
            if len(match) != 1:
                raise ValueError("Changed pure output assignment")
            part = replace_once(
                part, "value = " + match[0] + ";", "value = (" + match[0] + ") ^ 32'd1;"
            )
            text = text[:start] + part + text[end:]
        model = case / "actual.v"
        model.write_text(text + wrapper)
        binary = callback_order.compile_native(
            model,
            case,
            "FdcBitProbe",
            ROOT / "sim/native/bbc_fdc_bits_test.cpp",
            [ROOT / "sim/native/bbc_fm_media.h"],
        )
        trial = subprocess.run(
            [str(binary), str(DIRECTORY)], capture_output=True, text=True, timeout=120
        )
        log = case / "run.log"
        log.write_text(trial.stdout + trial.stderr)
        accepted = (
            trial.returncode == 0 and trial.stdout == SUMMARY and not trial.stderr
            if name == "positive"
            else trial.returncode == 1
            and not trial.stderr
            and re.fullmatch(prefix + r" [0-9]+(?: [0-9]+)?\n", trial.stdout) is not None
        )
        if not accepted:
            raise ValueError("Bit truth-table check failed: " + str(log))
        checks.append({"name": name, "accepted": True})
        artifacts.extend([model, binary, log, case / "build.log", case / "build.json"])
    frame = DIRECTORY / "frame.v"
    frame.write_text("""module ExtractFrame(input[31:0]bits,output equivalent);
wire[31:0]full,low;
FdcExtractSource a(.arg0(bits),.value(full));
FdcExtractSource b(.arg0({16'd0,bits[15:0]}),.value(low));
assign equivalent=full==low;
endmodule
""")
    query = DIRECTORY / "frame"
    query.mkdir(exist_ok=True)
    artifacts.extend(
        callback_cpu.prove(
            query,
            f"read_verilog {DIRECTORY / 'source.v'} {frame}; prep -top ExtractFrame -flatten; opt; check -assert",
            "equivalent",
            True,
            horizon=0,
            show="-show bits",
        )
    )
    artifacts.append(frame)
    checks.append({"name": "all-32-bit-extraction-frame", "accepted": True})
    return checks, artifacts


def native(multiplier, parent, top):
    selected = fdc_events.fixtures((DIRECTORY / "public/original.tsv").read_text(), fdc_order.NAMES)
    fixture = DIRECTORY / "public.tsv"
    fixture.write_text(re.sub(r"\Acase [0-9]+ ", "case 0 ", selected["rephased-byte-ack"]))
    observations = int(fixture.read_text().splitlines()[0].split()[-1])
    checks, artifacts = [], [fixture]
    for name, source in (
        ("positive", parent.read_text()),
        ("bad-header-crc", crc_fault(parent.read_text())),
    ):
        case = DIRECTORY / f"native-{name}{multiplier}"
        case.mkdir(exist_ok=True)
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
            [str(binary), str(fixture), str(ROOT / "build/bbc/disc.ssd"), str(multiplier), "1"],
            capture_output=True,
            text=True,
            timeout=600,
        )
        log = case / "run.log"
        log.write_text(trial.stdout + trial.stderr)
        accepted = (
            trial.returncode == 0
            and not trial.stderr
            and trial.stdout
            == f"FDC epoch: 1 cold cases; {observations} observations; 0 differences\n"
            if name == "positive"
            else fdc_events.negative_result(trial, "rephased-byte-ack")
        )
        if not accepted:
            raise ValueError("Actual CRC history failed: " + str(log))
        checks.append({"name": "native-" + name, "clock_multiplier": multiplier, "accepted": True})
        artifacts.extend([model, binary, log, case / "build.log", case / "build.json"])
    return checks, artifacts


def qualify():
    current_machine.audit()
    DIRECTORY.mkdir(parents=True, exist_ok=True)
    reference = source_root("jsbeeb")
    clocks = [current_machine.DIRECTORY / f"clock{m}/result.json" for m in (1, 3)]
    for clock in clocks:
        memory_composition.sealed(clock)
    helper = ROOT / "tools/bbc/fdc_bit_source.mjs"
    files = [
        Path(__file__),
        helper,
        ROOT / "sim/native/bbc_fdc_bits_test.cpp",
        ROOT / "sim/native/bbc_fdc_branch_test.cpp",
        ROOT / "sim/native/bbc_disc_payload.h",
        ROOT / "sim/native/bbc_fm_media.h",
        ROOT / "rtl/bbc/Intel8271.scala",
        ROOT / "tools/bbc/reference_drive.mjs",
        ROOT / "build/bbc/disc.ssd",
        ROOT / "node_modules/prettier/plugins/babel.mjs",
        fdc_order.__file__,
        *clocks,
        current_machine.DIRECTORY / "generation.json",
        *[p for _, p, _ in current_machine.parents()],
        *[p for _, parent, _ in current_machine.parents() for p in parent.parent.glob("*.bin")],
        *sorted((reference / "src").rglob("*.js")),
        *[
            Path(m.__file__)
            for m in (
                callback_cpu,
                callback_order,
                fdc_epoch,
                fdc_events,
                prepared,
                current_machine,
                memory_composition,
            )
        ],
        Path(fdc_order.__file__).with_suffix(".mjs"),
        ROOT / "tools/bbc/proofs/video_cursor.py",
        ROOT / "tools/validation/common.py",
        *[Path(shutil.which(t)) for t in ("node", "yosys", "verilator", "c++")],
    ]
    files = [Path(p) for p in files]
    before = hashes(files)
    subprocess.run(["node", str(helper), str(reference), str(DIRECTORY)], check=True, timeout=120)
    projection = json.loads((DIRECTORY / "projection.json").read_text())
    if projection.get("passed") is not True or [
        projection.get(k) for k in ("crc_cases", "fm_cases", "extract_cases")
    ] != [16777216, 65536, 65536]:
        raise ValueError("Incomplete original bit truth tables")
    checks, artifacts = kernels()
    public = DIRECTORY / "public"
    public.mkdir(exist_ok=True)
    subprocess.run(
        [
            "node",
            str(Path(fdc_order.__file__).with_suffix(".mjs")),
            str(reference),
            str(ROOT / "tools/bbc/reference_drive.mjs"),
            str(ROOT / "build/bbc/disc.ssd"),
            str(public),
        ],
        check=True,
        timeout=120,
    )
    fdc_order.original(public / "original.json")
    for multiplier, parent, top in current_machine.parents():
        for producer in (formal, native):
            c, a = producer(multiplier, parent, top)
            checks.extend(c)
            artifacts.extend(a)
            print(f"Source bits {multiplier} {producer.__name__}: PASS", flush=True)
    artifacts.extend(
        [DIRECTORY / n for n in ("source.v", "crc.bin", "fm.bin", "extract.bin", "projection.json")]
    )
    artifacts.extend([public / "original.tsv", public / "original.json"])
    current_machine.audit()
    stable = before == hashes(files)
    if not stable:
        raise ValueError("Bit qualification inputs changed")
    write_json(
        DIRECTORY / "result.json",
        {
            "passed": True,
            "inputs_unchanged": True,
            "inputs": before,
            "artifacts": hashes(artifacts),
            "checks": checks,
            "crc_cases": 16777216,
            "fm_cases": 65536,
            "extract_low_cases": 65536,
            "scope": "Original pure bitwise AST kernels, exhaustively checked against original execution over every CRC16/byte8 and FM clock8/data8 input. Source extraction accesses only masked low16 inputs; exhaustive low16 original execution and an all32-bit kernel frame theorem extend extraction equality. The actual media helper CRC is exhausted too. Three all-state actual 934-bit controller CRC update domains and a public cold read/rephase/ack history are checked at 32/96 MHz; primitive output and actual header CRC faults must fail behaviorally.",
            "limits": "CRC transition domains exclude host/reset/index interference and cover one header body position and eligible payload updates. Does not establish initialization, arbitrary byte/command scheduling, media framing, complete source state refinement, or whole-machine equivalence.",
            "full_equivalence": False,
            "universal_game_equivalence": False,
        },
    )


if __name__ == "__main__":
    qualify()
