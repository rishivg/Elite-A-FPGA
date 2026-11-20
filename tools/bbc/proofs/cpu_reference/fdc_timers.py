"""Source scheduler rearming and non-cancelling WRITE SPECIAL timer transitions."""

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

DIRECTORY = ROOT / "build/bbc/fdc-source-timers"
NAMES = [
    f"{kind}-{offset}" for kind in ("rate", "zero", "cancel", "replace") for offset in (-1, 0, 1)
]
FIELDS = {
    "mechanicalPhase": 2,
    "mechanicalDelay": 24,
    "idSector": 8,
    "mechanicalOp": 4,
    "idTrack": 8,
    "physicalTrack": 8,
    "physicalTrack1": 8,
    "driveOut": 8,
    "rotation": 23,
    "readyTimeout": 24,
    "readyPulses": 2,
    "stepRate": 8,
    "headRate": 8,
    "indexCallback": 2,
    "command": 8,
    "acceptingParameters": 1,
    "parameterCount": 8,
    "register0": 8,
    "parameters_0": 8,
    "parameters_1": 8,
    "result": 8,
    "busy": 1,
    "ready": 1,
    "nmiLevel": 1,
    "phase_1": 4,
    "dispatchPending": 1,
    "writeCrcStage": 3,
}
MECHANICAL = tuple(FIELDS)[:11]


def timer_fault(source):
    pattern = r"if\(writeSpecialStarts\) begin\n( +)mmioClocks <="
    matches = list(re.finditer(pattern, source))
    if len(matches) != 1:
        raise ValueError("Changed non-cancelling WRITE SPECIAL interface")
    match = matches[0]
    return replace_once(
        source,
        match[0],
        "if(writeSpecialStarts) begin\n"
        + match[1]
        + "mechanicalPhase <= Mechanical_Idle;\n"
        + match[1]
        + "mmioClocks <=",
    )


def state_indices(body):
    state = body["ports"]["proofState"]["bits"]
    if len(state) != 934 or len(set(state)) != 934:
        raise ValueError("Incomplete timer state inventory")
    result, used = {}, set()
    for name, width in FIELDS.items():
        bits = body["netnames"][name]["bits"]
        if len(bits) != width or not set(bits).issubset(state) or used.intersection(bits):
            raise ValueError("Missing or aliased timer state: " + name)
        result[name] = [state.index(bit) for bit in bits]
        used.update(bits)
    return result


def source_rates(report):
    values = report.get("step_ms")
    if (
        report.get("passed") is not True
        or report.get("cases") != 12288
        or not isinstance(values, list)
        or len(values) != 256
        or any(type(v) is not int or not 0 < v <= 510 for v in values)
    ):
        raise ValueError("Incomplete original scheduler/rate projection")
    return (
        "function[23:0] sourceStep(input[7:0]rate);begin case(rate)\n"
        + "\n".join(f"8'd{i}:sourceStep=24'd{v * 32000};" for i, v in enumerate(values))
        + "\nendcase end endfunction\n"
    )


def contract(body, enable, rates):
    fields = state_indices(body)

    def value(name, vector="proofState"):
        return "{" + ",".join(f"{vector}[{i}]" for i in reversed(fields[name])) + "}"

    inputs, actual, baseline = [], [], []
    for name, port in body["ports"].items():
        if port["direction"] == "input":
            inputs.append(f"input[{len(port['bits']) - 1}:0] {name}")
            actual.append(f".{name}({name})")
            signal = name if name not in ("io_write", "io_read", "io_warmReset") else "1'b0"
            baseline.append(f".{name}({signal})")
    equal = " && ".join(value(n, "nextA") + "==" + value(n, "nextB") for n in MECHANICAL)
    return f"""module TimerHistory({",".join(inputs)},output equivalent,writeEquivalent);
{rates}
wire[933:0]nextA,nextB;
wire indexExpiry,indexPulse,calibrationZero;
FdcStateNext actual({",".join(actual)},.proofNext(nextA),.timerIndexExpiry(indexExpiry),.timerIndexPulse(indexPulse),.timerCalibrationZero(calibrationZero));
FdcStateNext baseline({",".join(baseline)},.proofNext(nextB));
wire enabled={enable};
wire[3:0] phase={value("mechanicalPhase")};
wire[23:0]remaining={value("mechanicalDelay")};
wire[7:0]rate={value("stepRate")},cmd={value("command")},drive={value("driveOut")},head={value("headRate")};
wire nonfinal=phase==1 && {value("idSector")}!=0 && !(({value("idTrack")}==0) && calibrationZero);
wire parameterWrite=io_address==1 &&
 {value("acceptingParameters")} && {value("parameterCount")}==1 && {value("register0")}==6 &&
 cmd[5:2]==14 && cmd[7:6]==1;
wire zeroWrite=io_address==0 && io_data==8'h78;
wire inactiveByte={value("phase_1")}==0 && {value("writeCrcStage")}==0 && !{value("dispatchPending")};
wire writeDomain=inactiveByte && io_write && !io_read && !io_warmReset && (parameterWrite || zeroWrite) &&
 drive[7:6]==1 && drive[3] && head[7:4]==15 &&
 {value("indexCallback")}!=2 && {value("parameters_0")}==8'h0d &&
 (phase==1 || phase==2) && (remaining!=0 || nonfinal) && !indexExpiry;
assign writeEquivalent=reset || !enabled || !writeDomain ||
 ({equal} && {value("stepRate", "nextA")}==(zeroWrite ? {value("parameters_1")}:io_data) && !{value("busy", "nextA")} &&
 {value("ready", "nextA")}==zeroWrite && {value("result", "nextA")}==(zeroWrite ? 8'd0:{value("result", "nextB")}) && !{value("acceptingParameters", "nextA")} &&
 {value("nmiLevel", "nextA")}=={value("nmiLevel", "nextB")});
wire rearmDomain=inactiveByte && !io_read && !io_write && !io_warmReset && remaining==0 && nonfinal && !indexExpiry && !indexPulse;
wire rearm=reset || !enabled || !rearmDomain ||
 ({value("mechanicalDelay", "nextA")}==sourceStep(rate)-24'd1 && {value("mechanicalPhase", "nextA")}==1 &&
 {value("idSector", "nextA")}=={value("idSector")}-8'd1 &&
 {value("mechanicalOp", "nextA")}=={value("mechanicalOp")});
wire countdown=reset || !enabled || !inactiveByte || io_read || io_write || io_warmReset || remaining==0 ||
 !(phase==1 || phase==2) || {value("mechanicalDelay", "nextA")}==remaining-24'd1;
assign equivalent=writeEquivalent && rearm && countdown && (enabled || nextA==proofState);
endmodule
"""


def formal(multiplier, parent, top, rates):
    controller, extra = callback_order.fdc_instance(parent.read_text(), top)
    checks, artifacts = [], []
    for name, source in (
        ("positive", controller),
        ("cancel-write-special", timer_fault(controller)),
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
            ("timerIndexExpiry", "indexExpires"),
            ("timerIndexPulse", "index"),
            ("timerCalibrationZero", "calibrationAtZero"),
        ):
            body["ports"][port] = {"direction": "output", "bits": body["netnames"][signal]["bits"]}
        write_json(cut, data)
        wrapper.write_text(contract(body, extra[0] if extra else "1'b1", rates))
        logs = callback_cpu.prove(
            case,
            f"read_json {cut}; read_verilog {wrapper}; prep -top TimerHistory -flatten; memory_map; opt; check -assert; write_json {graph}",
            "equivalent" if name == "positive" else "writeEquivalent",
            name == "positive",
            horizon=0,
            show="-show-inputs -show phase -show remaining -show writeDomain",
        )
        callback_cpu.guard_inputs(
            json.loads(graph.read_text()),
            "TimerHistory",
            {n: len(p["bits"]) for n, p in body["ports"].items() if p["direction"] == "input"},
        )
        checks.append(
            {
                "clock_multiplier": multiplier,
                "name": "formal-" + name,
                "accepted": True,
                "register_bits": 934,
            }
        )
        artifacts.extend(
            [model, raw, cut, wrapper, graph, case / "prepared.json", case / "prepare.log", *logs]
        )
    return checks, artifacts


def negative_result(trial):
    return (
        trial.returncode == 1
        and not trial.stderr
        and re.fullmatch(
            r"FDC_EPOCH_MISMATCH rate--1 cycle [0-9]+ return 0/24 nmi 0/1 edges 0/1\n", trial.stdout
        )
        is not None
    )


def qualify():
    current_machine.audit()
    DIRECTORY.mkdir(parents=True, exist_ok=True)
    clocks = [current_machine.DIRECTORY / f"clock{m}/result.json" for m in (1, 3)]
    for path in clocks:
        memory_composition.sealed(path)
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
        ROOT / "build/bbc/disc.ssd",
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
    source = json.loads((DIRECTORY / "original.json").read_text())
    scheduler = json.loads((DIRECTORY / "scheduler.json").read_text())
    rates = source_rates(scheduler)
    if source.get("passed") is not True or [c["name"] for c in source["cases"]] != NAMES:
        raise ValueError("Incomplete original timer history inventory")
    table = DIRECTORY / "original.tsv"
    fdc_events.fixtures(table.read_text(), NAMES)
    observations = sum(c["rows"] for c in source["cases"])
    artifacts = [table, DIRECTORY / "original.json", DIRECTORY / "scheduler.json"]
    checks = []
    for multiplier, parent, top in current_machine.parents():
        c, a = formal(multiplier, parent, top, rates)
        checks.extend(c)
        artifacts.extend(a)
        for name, text in (
            ("positive", parent.read_text()),
            ("cancel-write-special", timer_fault(parent.read_text())),
        ):
            case = DIRECTORY / f"native-{name}{multiplier}"
            case.mkdir(exist_ok=True)
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
                driver,
                [ROOT / "sim/native/bbc_disc_payload.h", ROOT / "sim/native/bbc_fm_media.h"],
            )
            trial = subprocess.run(
                [str(binary), str(table), str(ROOT / "build/bbc/disc.ssd"), str(multiplier), "12"],
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
                    == f"FDC epoch: 12 cold cases; {observations} observations; 0 differences\n"
                )
                if name == "positive"
                else negative_result(trial)
            )
            checks.append(
                {"clock_multiplier": multiplier, "name": "native-" + name, "accepted": accepted}
            )
            artifacts.extend([model, binary, log, case / "build.json", case / "build.log"])
            print(
                f"Timer history {multiplier} {name}: " + ("PASS" if accepted else "FAIL"),
                flush=True,
            )
            if not accepted:
                raise ValueError("Timer history differs: " + str(log))
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
            "source_cases": 12,
            "source_scheduler_cases": 12288,
            "observations_per_clock": observations,
            "scope": "All-state 934-bit actual FDC transition queries at 32/96 MHz: a valid final WRITE SPECIAL step-rate parameter or zero-parameter WRITE SPECIAL retains the pending mechanical transition, including an old step rearming on that edge; busy clears, the new rate is stored, result/ready semantics follow the access type, and no CPU event is lost. All old state is symbolic under the declared same-drive loaded-head, never-unload, no simultaneous completion/index-timeout domain. Eligible later seek steps without a simultaneous index pulse reload their delay from every original 8-bit step-rate value; active counters decrement and off-base state holds. The maintained original executes 12288 finite scheduler cancellation/rearm projections, including queued timers labelled none and coincident deadlines, and twelve public cold histories covering live RAM rate programming, zero-parameter WRITE SPECIAL, READ DRIVE STATUS cancellation and replacement SEEK ownership before/on/after a step deadline. Old timer deadlines cannot reappear after the replacement completion is acknowledged. The old cancellation fault must fail both the transition property and exact public event observation at both rates.",
            "limits": "Finite original projections are not a universal JavaScript translation or an unbounded source/RTL scheduler-state relation. Native histories use the declared SSD/clocked-FM cold epoch and actual parent bus/detector with CPU instructions replaced by held requests. This certificate covers internal RAM programming while a mechanical phase is active; the separate fdc-next-five certificate covers MMIO and its acceptance-edge rotation adjustment. The local theorem requires the parameter callback to remain active through this edge and excludes simultaneous finishing/index callbacks; parameter acceptance on simultaneous completion remains separate. MMIO/SPECIFY overlap, live head/settle programming, completion-edge parameter acceptance and normal retry/track translation are covered separately by fdc-next-five. Arbitrary register-alias/media histories, external CPU warm resets and whole-game/board equivalence remain open.",
            "universal_game_equivalence": False,
        },
    )
    if not stable:
        raise ValueError("Timer proof inputs changed")


if __name__ == "__main__":
    qualify()
