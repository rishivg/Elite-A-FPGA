"""Mechanical callback ownership under partial replacement commands."""

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
from tools.bbc.proofs.video_cursor import hashes
from tools.validation.common import ROOT, source_root, write_json

DIRECTORY = ROOT / "build/bbc/fdc-source-commands"
NAMES = [f"{op}-partial-{offset}" for op in ("read", "seek") for offset in (-1, 0, 1)]
FIELDS = {
    "mechanicalPhase": 2,
    "mechanicalDelay": 24,
    "idSector": 8,
    "idTrack": 8,
    "mechanicalOp": 4,
    "command": 8,
    "latchedReady": 1,
    "result": 8,
    "ready": 1,
    "busy": 1,
    "nmiLevel": 1,
    "phase_1": 4,
    "dispatchPending": 1,
    "parameters_1": 8,
}


def context_fault(source):
    needle = "mechanicalOp == 4'b1010"
    if source.count(needle) != 4:
        raise ValueError("Changed mechanical completion context consumers")
    return source.replace(needle, "op == 4'b1010")


def indices(body):
    state = body["ports"]["proofState"]["bits"]
    if len(state) != 934 or len(set(state)) != 934:
        raise ValueError("Incomplete command controller state")
    found, used = {}, set()
    for name, width in FIELDS.items():
        bits = body["netnames"][name]["bits"]
        if len(bits) != width or not set(bits).issubset(state) or used.intersection(bits):
            raise ValueError("Changed command context state: " + name)
        found[name] = [state.index(bit) for bit in bits]
        used.update(bits)
    return found


def contract(body, enable):
    fields = indices(body)
    inputs, connections = [], []
    for name, port in body["ports"].items():
        if port["direction"] == "input":
            inputs.append(f"input[{len(port['bits']) - 1}:0] {name}")
            connections.append(f".{name}({name})")

    def field(name, vector="proofState"):
        return "{" + ",".join(f"{vector}[{i}]" for i in reversed(fields[name])) + "}"

    return f"""module MechanicalContext({",".join(inputs)},output equivalent,deadlineEquivalent);
wire[933:0] following;
wire callbackNmi,eventReady,eventBusy,indexExpiry,indexPulse,calibrationZero,abortEvent;
wire[23:0] postDelay;
wire[7:0] eventResult,driveIn;
FdcStateNext actual({",".join(connections)},.proofNext(following),.proofEvent(callbackNmi),.proofAbort(abortEvent),
 .contextReady(eventReady),.contextBusy(eventBusy),.contextResult(eventResult),
 .contextDriveIn(driveIn),.contextIndexExpiry(indexExpiry),.contextIndexPulse(indexPulse),.contextCalibrationZero(calibrationZero),.contextPostDelay(postDelay));
wire enabled={enable};
wire[3:0] phase={field("mechanicalPhase")},owner={field("mechanicalOp")};
wire[23:0] remaining={field("mechanicalDelay")};
wire finishing=remaining==0 && (phase==2 ||
 (phase==1 && postDelay==0 &&
 ({field("idSector")}==0 || (({field("idTrack")}==0) && calibrationZero))));
wire domain={field("phase_1")}==0 && !{field("dispatchPending")} && !io_read && !io_write && !io_warmReset && !indexExpiry && !indexPulse && (owner<=7 || owner==10);
wire ready=driveIn[2] && {field("latchedReady")};
wire complete=!ready || owner==10;
wire[7:0] expectedResult={field("result")} | (ready ? 8'd0:8'h10);
wire[3:0] expectedPhase=complete ? 0:(owner==6 ? 0:7);
assign deadlineEquivalent=reset || !enabled || !domain || !finishing ||
 (callbackNmi==({field("nmiLevel")} || complete) &&
 eventBusy==({field("busy")} && !complete) && eventReady==({field("ready")} || complete) &&
 eventResult==expectedResult && !abortEvent && {field("phase_1", "following")}==expectedPhase);
wire partial=io_write && io_address==0 && (!(|io_data[4:3]) || (|io_data[1:0]));
wire ownerHeld=reset || io_warmReset || {field("dispatchPending")} || !(phase==1 || phase==2) || !partial ||
 {field("mechanicalOp", "following")}==owner;
wire[7:0] currentCommand={field("command")};
wire ownerCaptured=reset || !enabled || io_warmReset || io_read || io_write || !{field("dispatchPending")} || !((currentCommand[5:2]<=7 || currentCommand[5:2]==10) && !(currentCommand[5:2]==6 && {field("parameters_1")}!=0)) ||
 {field("mechanicalOp", "following")}==currentCommand[5:2];
assign equivalent=deadlineEquivalent && ownerHeld && ownerCaptured && (enabled || following==proofState);
endmodule
"""


def formal(multiplier, parent, top):
    controller, extra = callback_order.fdc_instance(parent.read_text(), top)
    for name, value in (("Idle", 0), ("Seek", 1), ("Delay", 2)):
        if controller.count(f"localparam Mechanical_{name} = 2'd{value};") != 1:
            raise ValueError("Changed mechanical phase encoding")
    artifacts, checks = [], []
    for name, source in (
        ("positive", controller),
        ("new-command-context", context_fault(controller)),
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
                source,
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
        for port, signal in (
            ("Ready", "eventReady"),
            ("Busy", "eventBusy"),
            ("Result", "eventResult"),
            ("DriveIn", "driveIn"),
            ("IndexExpiry", "indexExpires"),
            ("IndexPulse", "index"),
            ("CalibrationZero", "calibrationAtZero"),
            ("PostDelay", "seekPostDelay"),
        ):
            body["ports"]["context" + port] = {
                "direction": "output",
                "bits": body["netnames"][signal]["bits"],
            }
        write_json(cut, data)
        wrapper.write_text(contract(body, extra[0] if extra else "1'b1"))
        logs = callback_cpu.prove(
            case,
            f"read_json {cut}; read_verilog {wrapper}; prep -top MechanicalContext -flatten; opt; check -assert; write_json {graph}",
            "equivalent" if name == "positive" else "deadlineEquivalent",
            name == "positive",
            horizon=0,
            show="-show-inputs -show owner -show phase -show complete -show finishing",
        )
        callback_cpu.guard_inputs(
            json.loads(graph.read_text()),
            "MechanicalContext",
            {n: len(p["bits"]) for n, p in body["ports"].items() if p["direction"] == "input"},
        )
        artifacts.extend(
            [model, raw, cut, wrapper, graph, case / "prepared.json", case / "prepare.log", *logs]
        )
        checks.append(
            {
                "clock_multiplier": multiplier,
                "name": "formal-" + name,
                "accepted": True,
                "register_bits": 934,
            }
        )
    return checks, artifacts


def negative_result(trial):
    return (
        trial.returncode == 1
        and not trial.stderr
        and re.fullmatch(
            r"FDC_EPOCH_MISMATCH read-partial--1 cycle [0-9]+ return 24/144 nmi 1/0 edges 1/0\n",
            trial.stdout,
        )
        is not None
    )


def qualify():
    current_machine.audit()
    clocks = [current_machine.DIRECTORY / f"clock{m}/result.json" for m in (1, 3)]
    for path in clocks:
        memory_composition.sealed(path)
    DIRECTORY.mkdir(parents=True, exist_ok=True)
    reference = source_root("jsbeeb")
    helper = Path(__file__).with_suffix(".mjs")
    driver = ROOT / "sim/native/bbc_fdc_branch_test.cpp"
    files = [
        Path(__file__),
        helper,
        driver,
        ROOT / "sim/native/bbc_disc_payload.h",
        ROOT / "sim/native/bbc_fm_media.h",
        ROOT / "tools/bbc/reference_drive.mjs",
        ROOT / "rtl/bbc/Intel8271.scala",
        ROOT / "build/bbc/disc.ssd",
        *cpu_events.files(),
        *[ROOT / name for name in current_machine.generation_inputs()],
        *clocks,
        current_machine.DIRECTORY / "generation.json",
        *[p for _, p, _ in current_machine.parents()],
        *[blob for _, p, _ in current_machine.parents() for blob in p.parent.glob("*.bin")],
        *sorted((reference / "src").rglob("*.js")),
        *[
            Path(m.__file__)
            for m in (
                bus_binding_proof,
                cpu_events,
                cpu_interrupt_proof,
                current_machine,
                memory_composition,
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
        timeout=60,
    )
    original = json.loads((DIRECTORY / "original.json").read_text())
    projection = json.loads((DIRECTORY / "projection.json").read_text())
    if (
        original.get("passed") is not True
        or [c["name"] for c in original["cases"]] != NAMES
        or projection != {"passed": True, "cases": 524288}
    ):
        raise ValueError("Incomplete original command context projection")
    table = DIRECTORY / "original.tsv"
    fdc_events.fixtures(table.read_text(), NAMES)
    observations = sum(c["rows"] for c in original["cases"])
    artifacts = [table, DIRECTORY / "original.json", DIRECTORY / "projection.json"]
    checks = []
    for multiplier, parent, top in current_machine.parents():
        c, a = formal(multiplier, parent, top)
        checks.extend(c)
        artifacts.extend(a)
        for name, source in (
            ("positive", parent.read_text()),
            ("new-command-context", context_fault(parent.read_text())),
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
                driver,
                [ROOT / "sim/native/bbc_disc_payload.h", ROOT / "sim/native/bbc_fm_media.h"],
            )
            trial = subprocess.run(
                [str(binary), str(table), str(ROOT / "build/bbc/disc.ssd"), str(multiplier), "6"],
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
                    == f"FDC epoch: 6 cold cases; {observations} observations; 0 differences\n"
                )
                if name == "positive"
                else negative_result(trial)
            )
            checks.append(
                {"clock_multiplier": multiplier, "name": "native-" + name, "accepted": accepted}
            )
            artifacts.extend([model, binary, log, case / "build.json", case / "build.log"])
            print(
                f"Command context {multiplier} {name}: " + ("PASS" if accepted else "FAIL"),
                flush=True,
            )
            if not accepted:
                raise ValueError("Command callback ownership differs: " + str(log))
    current_machine.audit()
    for path in clocks:
        memory_composition.sealed(path)
    stable = before == hashes(files)
    write_json(
        DIRECTORY / "result.json",
        {
            "passed": stable,
            "inputs_unchanged": stable,
            "inputs": before,
            "artifacts": hashes(artifacts),
            "checks": checks,
            "source_cases": 6,
            "source_projection_cases": 524288,
            "observations_per_clock": observations,
            "scope": "Actual 32/96 MHz mechanical completion decisions retain the dispatched operation despite an arbitrary newer command register. All-state SAT quantifies all 934 Q/D bits: delay expiry or the final zero-settle seek step completes SEEK or ready failure and otherwise enters READ/WRITE/READ ID search, preserving result/ready/busy/NMI semantics. Partial command writes preserve the stored owner; disabled state holds. The original executes 524288 finite context/current-command/ready/result projections. Six public cold histories use actual parent clocks, decoder, strobes and every physical CPU detector sample, leaving a replacement READ or SEEK incomplete before/on/after the old settle deadline. Restoring the old context selection must fail formally and in the exact public NMI/status observation at both rates.",
            "limits": "Original private-state projections are finite executions, not a universal JavaScript translation. The RTL completion theorem excludes simultaneous host access, external reset or any simultaneous index pulse; command/result/storage and all other old state are symbolic. The public cases share the maintained cold epoch and SSD/clocked-FM drive, with instruction execution replaced by held requests. The fdc-next-five certificate separately covers waiting SPECIFY setup, callback-cleared parameter acceptance and normal retry/track translation. Arbitrary overlapping register-alias/media histories, external CPU warm reset and whole-game/board equivalence remain open.",
            "universal_game_equivalence": False,
        },
    )
    if not stable:
        raise ValueError("Command context proof inputs changed")


if __name__ == "__main__":
    qualify()
