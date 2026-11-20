"""Full-byte shared R1 and mechanically lowered original seek arithmetic."""

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

DIRECTORY = ROOT / "build/bbc/fdc-shared-registers"
WIDTH = 934
NAMES = [
    *[f"r1-{count}-{offset}" for count in (1, 2, 3) for offset in (-1, 0, 1)],
    *[f"seek-bank-{address}-{offset}" for address in (10, 11, 12) for offset in (100, 4000, 8000)],
    *[f"spec-pointer-{offset}" for offset in (-1, 0, 1)],
    *[f"spec-r1-{value}" for value in (0, 1, 2, 3, 255)],
    *[f"r1-param-{offset}" for offset in (-1, 0, 1)],
    *[f"index-register-{value}" for value in (0, 1, 2, 3, 255)],
    "programmed-transfer",
    *[f"retry-owner-{offset}" for offset in (-1, 0, 1)],
]
NATIVE_FAULTS = {
    "stale-counter": "r1-1--1",
    "stale-target": "r1-1--1",
    "stale-owner": "retry-owner--1",
    "stale-pointer": "spec-pointer-0",
    "stale-seek": "seek-bank-10-4000",
    "narrow-index": "index-register-0",
}
FIELDS = {
    "parameterCount": 8,
    "parameters_0": 8,
    "parameters_1": 8,
    "driveOut": 8,
    "badTrack00": 8,
    "badTrack01": 8,
    "badTrack10": 8,
    "badTrack11": 8,
    "phase_1": 4,
    "mechanicalPhase": 2,
    "mechanicalDelay": 24,
    "mechanicalOp": 4,
    "dispatchPending": 1,
    "executingOp": 4,
    "command": 8,
    "mode": 8,
    "rotation": 23,
    "headerCrc": 16,
    "headerCrcHigh": 8,
    "idTrack": 8,
    "currentTrack0": 8,
    "currentTrack1": 8,
    "register0": 8,
    "indexCount": 8,
    "indexCallback": 2,
    "headRate": 8,
    "idSector": 8,
    "delay": 24,
    "physicalTrack": 8,
    "physicalTrack1": 8,
}
EXPORTS = {
    "target": "retryTarget",
    "nextTarget": "nextRetryTarget",
    "expiry": "indexExpires",
    "headerPresent": "headerPresent",
    "parsing": "parsingHeader",
    "crcClock": "crcClock",
    "eventPointer": "eventRegister0",
    "eventCounter": "eventParameterCount",
    "eventAccept": "eventAcceptingParameters",
    "eventSpec": "eventSpecifying",
    "indexPulse": "index",
    "atZero": "calibrationAtZero",
}
PROPERTIES = (
    "targetEquivalent",
    "counterEquivalent",
    "snapshotEquivalent",
    "ownerEquivalent",
    "parameterEquivalent",
    "seekCountEquivalent",
    "indexEquivalent",
)


def indices(body):
    state = body["ports"]["proofState"]["bits"]
    if (
        len(state) != WIDTH
        or len(set(state)) != WIDTH
        or len(body["ports"]["proofNext"]["bits"]) != WIDTH
    ):
        raise ValueError("Incomplete shared-register controller state")
    found, used = {}, set()
    for name, width in FIELDS.items():
        bits = body["netnames"][name]["bits"]
        if len(bits) != width or not set(bits).issubset(state) or used.intersection(bits):
            raise ValueError("Changed canonical controller field: " + name)
        found[name] = [state.index(bit) for bit in bits]
        used.update(bits)
    if "seekRetry" in body["netnames"]:
        raise ValueError("Controller regained a private retry counter")
    return found


def contract(body, enable):
    fields = indices(body)

    def q(name, vector="proofState"):
        return "{" + ",".join(f"{vector}[{bit}]" for bit in reversed(fields[name])) + "}"

    inputs, ports = [], []
    for name, port in body["ports"].items():
        if port["direction"] == "input":
            inputs.append(f"input[{len(port['bits']) - 1}:0] {name}")
            ports.append(f".{name}({name})")
    exports = []
    declarations = []
    for name, signal in EXPORTS.items():
        width = len(body["netnames"][signal]["bits"])
        declarations.append(f"wire[{width - 1}:0] {name};")
        exports.append(f".shared_{name}({name})")
    regs = ["8'd0"] * 32
    for address, name in (
        (1, "parameterCount"),
        (7, "parameters_0"),
        (16, "badTrack00"),
        (17, "badTrack01"),
        (24, "badTrack10"),
        (25, "badTrack11"),
    ):
        regs[address] = q(name)
    source = "{" + ",".join(reversed(regs)) + "}"
    regs[1] = "incremented"
    after = "{" + ",".join(reversed(regs)) + "}"
    return f"""module SharedRegisters({",".join(inputs)},input[31:0]sourceCall,output {",".join(PROPERTIES)});
wire[{WIDTH - 1}:0]following;
{"".join(declarations)}
FdcStateNext actual({",".join(ports)},.proofNext(following),{",".join(exports)});
wire[7:0]count={q("parameterCount")},incremented=count+8'd1,drive={q("driveOut")},cmd={q("command")};
wire[7:0]originalTarget,originalNextTarget;
wire[31:0]unclamped,unclampedNext;
FdcSeekSource original(.registers({source}),.driveOut(drive),.callContext(sourceCall),.target(originalTarget),.unclamped(unclamped));
FdcSeekSource originalAfterIncrement(.registers({after}),.driveOut(drive),.callContext(sourceCall),.target(originalNextTarget),.unclamped(unclampedNext));
assign targetEquivalent=target==originalTarget && nextTarget==originalNextTarget && unclamped<256 && unclampedNext<256;
wire[3:0]phase={q("phase_1")},owner={q("executingOp")};
wire[7:0]mode={q("mode")};
wire[15:0]crc={q("headerCrc")},supplied={{{q("headerCrcHigh")},io_storageData[7:0]}};
wire mismatch={enable} && !reset && !io_warmReset && !expiry && phase==7 && {q("mechanicalPhase")}==0 && !{q("dispatchPending")} && {q("mechanicalOp")}==owner && owner<=7 && owner!=6 &&
 mode[5:4]==0 && cmd!=8'h18 && headerPresent && parsing && {q("rotation")}==crcClock-1 && crc==supplied && {q("idTrack")}!={q("parameters_0")};
wire[7:0]newParameters=io_data[4:3]!=0 ? {{6'd0,io_data[1:0]}}:8'd5;
wire hostCommand=io_write && !io_read && io_address==0 && newParameters!=0;
wire domain=mismatch && !io_read && (!io_write || hostCommand);
assign counterEquivalent=!domain || {q("parameterCount", "following")}==(hostCommand?newParameters:incremented);
wire terminal=incremented==3;
wire[7:0]oldTrack=drive[7]?{q("currentTrack1")}:{q("currentTrack0")};
wire[7:0]newTrack=drive[7]?{q("currentTrack1", "following")}:{q("currentTrack0", "following")};
assign snapshotEquivalent=!domain || newTrack==(terminal?oldTrack:originalNextTarget);
assign ownerEquivalent=!domain || {q("mechanicalOp", "following")}=={q("mechanicalOp")};
wire[255:0]parameterBefore={{240'd0,eventCounter,eventPointer}},parameterAfter;
wire[1:0]callback=eventAccept?2'd1:(eventSpec?2'd2:2'd0);
wire started,finished,trap;
FdcParameterSource originalParameter(.registers(parameterBefore),.param(io_data),.callback(callback),.following(parameterAfter),.started(started),.finished(finished),.trap(trap));
wire parameterDomain={enable} && !reset && !io_warmReset && io_write && !io_read && io_address==1 && callback!=0 && !started;
assign parameterEquivalent=!parameterDomain || (!trap && {q("register0", "following")}==parameterAfter[7:0] && {q("parameterCount", "following")}==parameterAfter[15:8]);
wire[7:0]remaining={q("idSector")};
wire stop=remaining==0 || ({q("idTrack")}==0 && atZero);
wire noHost=!io_read && !io_write && !io_warmReset;
wire seekDomain={enable} && !reset && noHost && !indexPulse && phase==0 && {q("mechanicalPhase")}==1 && !{q("dispatchPending")} && {q("mechanicalDelay")}==0;
assign seekCountEquivalent=!seekDomain || {q("idSector", "following")}==(stop?remaining:remaining-8'd1);
wire[1:0]indexKind={q("indexCallback")};
wire indexDomain={enable} && !reset && noHost && indexPulse && drive[3] && drive[7:6]==1 &&
 phase==0 && {q("mechanicalPhase")}==0 && !{q("dispatchPending")} && (indexKind==1 || indexKind==2);
wire[7:0]head={q("headRate")};
wire unloadingAfterTimeout=indexKind==1 && {q("indexCount")}==1 && head[7:4]!=0 && head[7:4]!=15;
assign indexEquivalent=!indexDomain || {q("indexCount", "following")}==(unloadingAfterTimeout?{{4'd0,head[7:4]}}:{q("indexCount")}-8'd1);
endmodule
"""


def fault(source, name):
    if name == "stale-counter":
        match = re.findall(r"  assign nextRetryCount = [^;]+;", source)
        if len(match) != 1:
            raise ValueError("Changed canonical retry increment")
        return replace_once(source, match[0], "  assign nextRetryCount = 8'h01;")
    if name == "stale-target":
        match = re.findall(r"  assign nextRetryTarget = [^;]+;", source)
        if len(match) != 1:
            raise ValueError("Changed retry target binding")
        return replace_once(source, match[0], "  assign nextRetryTarget = parameters_0;")
    if name == "stale-owner":
        target = "parameterCount <= nextRetryCount;"
        if source.count(target) != 2:
            raise ValueError("Changed shared ID CRC retry sites")
        return source.replace(target, target + "\nmechanicalOp <= op;")
    if name == "stale-pointer":
        return replace_once(
            source,
            "assign parameterPointer = eventRegister0[5:0];",
            "assign parameterPointer = register0[5:0];",
        )
    if name == "stale-seek":
        match = re.findall(r"  assign \w+ = \(stepsRemaining - 8'h01\);", source)
        if len(match) != 1:
            raise ValueError("Changed remaining-step decrement")
        return replace_once(
            source, match[0], re.sub(r"\(stepsRemaining - 8'h01\)", "stepsRemaining", match[0])
        )
    if name == "narrow-index":
        old = "indexCount <= (preIndexCount - 8'h01);"
        if source.count(old) != 2:
            raise ValueError("Changed shared index-counter decrements")
        return source.replace(old, "indexCount <= ((preIndexCount - 8'h01) & 8'h03);")
    raise ValueError("Unknown shared-register fault")


def formal(multiplier, parent, top):
    controller, extra = callback_order.fdc_instance(parent.read_text(), top)
    artifacts, checks = [], []
    for name, text, prop in (
        ("positive", controller, None),
        ("stale-counter", fault(controller, "stale-counter"), "counterEquivalent"),
        ("stale-target", fault(controller, "stale-target"), "snapshotEquivalent"),
        ("stale-owner", fault(controller, "stale-owner"), "ownerEquivalent"),
        ("stale-pointer", fault(controller, "stale-pointer"), "parameterEquivalent"),
        ("stale-seek", fault(controller, "stale-seek"), "seekCountEquivalent"),
        ("narrow-index", fault(controller, "narrow-index"), "indexEquivalent"),
    ):
        case = DIRECTORY / f"formal-{name}{multiplier}"
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
        for key, signal in EXPORTS.items():
            body["ports"]["shared_" + key] = {
                "direction": "output",
                "bits": body["netnames"][signal]["bits"],
            }
        write_json(cut, data)
        wrapper.write_text(contract(body, extra[0] if extra else "1'b1"))
        lower = case / "lower"
        lower.mkdir(exist_ok=True)
        source = DIRECTORY / "seek-source.v"
        parameter = DIRECTORY / "parameter-source.v"
        prepared.prepare(
            lower,
            f"read_json {cut}; read_verilog -sv {source} {parameter} {wrapper}; prep -top SharedRegisters -flatten; opt; check -assert; write_json {graph}",
            [cut, source, parameter, wrapper],
            [graph],
        )
        callback_cpu.guard_inputs(
            json.loads(graph.read_text()),
            "SharedRegisters",
            {
                **{
                    n: len(p["bits"]) for n, p in body["ports"].items() if p["direction"] == "input"
                },
                "sourceCall": 32,
            },
        )
        for property_name in PROPERTIES if prop is None else (prop,):
            query = case / property_name
            query.mkdir(exist_ok=True)
            logs = callback_cpu.prove(
                query,
                f"read_json {graph}",
                property_name,
                prop is None,
                horizon=0,
                show="-show count -show incremented -show phase -show owner -show cmd -show io_data -show io_address",
            )
            checks.append(
                {
                    "clock_multiplier": multiplier,
                    "name": name,
                    "property": property_name,
                    "accepted": True,
                }
            )
            artifacts.extend(logs)
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


def negative_result(trial, name):
    return fdc_events.negative_result(trial, NATIVE_FAULTS[name])


def native(multiplier, parent, top, table, observations):
    selected = fdc_events.fixtures(table.read_text(), NAMES)
    variants = [("positive", table, len(NAMES))]
    artifacts, checks = [], []
    for name, fixture_name in NATIVE_FAULTS.items():
        path = DIRECTORY / (name + ".tsv")
        path.write_text(re.sub(r"\Acase [0-9]+ ", "case 0 ", selected[fixture_name]))
        artifacts.append(path)
        variants.append((name, path, 1))
    for name, fixture, count in variants:
        case = DIRECTORY / f"native-{name}{multiplier}"
        case.mkdir(exist_ok=True)
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
                == f"FDC epoch: {count} cold cases; {observations} observations; 0 differences\n"
            )
            if name == "positive"
            else negative_result(trial, name)
        )
        checks.append(
            {"clock_multiplier": multiplier, "name": "native-" + name, "accepted": accepted}
        )
        artifacts.extend([model, binary, log, case / "build.json", case / "build.log"])
        print(
            f"Shared FDC registers {multiplier} {name}: " + ("PASS" if accepted else "FAIL"),
            flush=True,
        )
        if not accepted:
            raise ValueError("Shared-register public history differs: " + str(log))
    return checks, artifacts


def source_checks(reference):
    artifacts = []
    for prefix, producer, driver, expected in (
        (
            "seek",
            "fdc_seek_source.mjs",
            "bbc_fdc_seek_source_test.cpp",
            "Original seek arithmetic: 524288 fixtures; 0 differences\n",
        ),
        (
            "parameter",
            "fdc_parameter_source.mjs",
            "bbc_fdc_parameter_source_test.cpp",
            "Original parameter acceptance: 524288 fixtures; 0 differences\n",
        ),
    ):
        helper = ROOT / "tools/bbc" / producer
        subprocess.run(
            ["node", str(helper), str(reference), str(DIRECTORY)], check=True, timeout=120
        )
        case = DIRECTORY / (prefix + "-native")
        case.mkdir(exist_ok=True)
        source = DIRECTORY / (prefix + "-source.v")
        top = "FdcSeekSource" if prefix == "seek" else "FdcParameterSource"
        binary = callback_order.compile_native(source, case, top, ROOT / "sim/native" / driver)
        trial = subprocess.run(
            [str(binary), str(DIRECTORY / (prefix + "-fixtures.tsv"))],
            capture_output=True,
            text=True,
            timeout=60,
        )
        log = case / "run.log"
        log.write_text(trial.stdout + trial.stderr)
        if trial.returncode or trial.stderr or trial.stdout != expected:
            raise ValueError("Lowered original " + prefix + " differs")
        artifacts.extend(
            [
                source,
                DIRECTORY / (prefix + "-fixtures.tsv"),
                DIRECTORY / (prefix + "-projection.json"),
                binary,
                log,
                case / "build.log",
                case / "build.json",
            ]
        )
    artifacts.append(DIRECTORY / "seek-prefix.js")
    return artifacts


def qualify():
    current_machine.audit()
    DIRECTORY.mkdir(parents=True, exist_ok=True)
    clocks = [current_machine.DIRECTORY / f"clock{m}/result.json" for m in (1, 3)]
    for clock in clocks:
        memory_composition.sealed(clock)
    reference = source_root("jsbeeb")
    helper = Path(__file__).with_name("fdc_alias_history.mjs")
    files = [
        Path(__file__),
        helper,
        ROOT / "tools/bbc/fdc_seek_source.mjs",
        ROOT / "tools/bbc/fdc_parameter_source.mjs",
        ROOT / "sim/native/bbc_fdc_seek_source_test.cpp",
        ROOT / "sim/native/bbc_fdc_parameter_source_test.cpp",
        ROOT / "sim/native/bbc_fdc_branch_test.cpp",
        ROOT / "sim/native/bbc_disc_payload.h",
        ROOT / "sim/native/bbc_fm_media.h",
        ROOT / "tools/bbc/reference_drive.mjs",
        ROOT / "build/bbc/disc.ssd",
        ROOT / "package-lock.json",
        *sorted((ROOT / "node_modules/prettier/plugins").glob("*.mjs")),
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
    artifacts = source_checks(reference)
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
        timeout=120,
    )
    original = json.loads((DIRECTORY / "original.json").read_text())
    if original.get("passed") is not True or [c["name"] for c in original["cases"]] != NAMES:
        raise ValueError("Incomplete shared-register history inventory")
    table = DIRECTORY / "original.tsv"
    fdc_events.fixtures(table.read_text(), NAMES)
    observations = sum(c["rows"] for c in original["cases"])
    artifacts.extend([table, DIRECTORY / "original.json"])
    checks = []
    for multiplier, parent, top in current_machine.parents():
        c, a = formal(multiplier, parent, top)
        checks.extend(c)
        artifacts.extend(a)
        c, a = native(multiplier, parent, top, table, observations)
        checks.extend(c)
        artifacts.extend(a)
    current_machine.audit()
    for clock in clocks:
        memory_composition.sealed(clock)
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
            "source_cases": len(NAMES),
            "observations_per_clock": observations,
            "scope": "All-state actual 934-bit controller transition checks at 32/96 MHz: the full-byte shared parameter/retry counter, same-edge host command priority, retry target snapshots and retained callback ownership, original parameter acceptance through canonical R0/R1 aliases before dispatch, remaining seek-count semantics and byte-wide index callbacks. Original seek arithmetic and parameter acceptance are mechanically lowered from the pinned Babel AST; each projection is independently checked against 524288 executing-original fixtures. Six behavioral faults must fail both formal properties and named public histories. Thirty-eight cold public histories per clock cover command/parameter interference around retries, live seek-register reads, SPECIFY pointer changes from mechanical callbacks, counter self-aliases and wrap, index programming with busy cleared, and continuing late-DMA aborts after WRITE SPECIAL.",
            "limits": "These are source-derived scalar projections and local actual-controller relations, not full source/FDC state correspondence. The parameter kernel exposes dispatch, finish, external-status and MMIO boundaries; comparison stops before a new command dispatch and retains the R0/R1 frame of normal-returning finish/MMIO helpers. Seek-count queries exclude concurrent host/index callbacks. Index-count queries cover idle bit-processor callbacks, including timeout-to-unload replacement; complete coincident timer/index/serial composition remains open. Public histories use the declared SSD/clocked-FM transport and held requests instead of instruction execution. Arbitrary media/formatting, complete controller callback scheduling, ROM eligibility, whole-machine composition and whole-game/board equivalence remain open.",
            "universal_game_equivalence": False,
        },
    )
    if not stable:
        raise ValueError("Shared-register qualification inputs changed")


if __name__ == "__main__":
    qualify()
