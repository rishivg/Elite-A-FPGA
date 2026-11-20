"""Original public zero-time commands versus actual FDC and physical CPU sampling."""

import json
import re
import shutil
import subprocess
from pathlib import Path

from tools.bbc import bus_binding_proof, cpu_events, cpu_interrupt_proof
from tools.bbc.proofs import current_machine, memory_composition
from tools.bbc.proofs.cpu_reference import callback_order
from tools.bbc.proofs.video_cursor import hashes, replace_once
from tools.validation.common import ROOT, source_root, write_json

DIRECTORY = ROOT / "build/bbc/cpu-zero-dispatch"


def native_model(source, top):
    fdc, extra = callback_order.fdc_instance(source, top)
    fdc = replace_once(
        fdc,
        "module Intel8271 (",
        "module Intel8271 (output[7:0]proofStoredStatus,output proofStoredNmi,",
    )
    fdc = replace_once(
        fdc,
        "endmodule",
        "assign proofStoredStatus={busy,2'b00,ready,interrupt,request,2'b00};\nassign proofStoredNmi=nmiLevel;\nendmodule",
    )
    core = cpu_events.observed({"PreviousNmi": ("", "NMI_1")})
    enable = f",.{extra[0]}(base)" if extra else ""
    wrapper = f"""module DispatchProbe(input clk,reset,base,rd,wr,input[2:0]address,input[7:0]data,
output[7:0]result,storedStatus,output nmi,storedNmi,previous);
wire retrigger;
Intel8271 f(.clk(clk),.reset(reset),.io_warmReset(1'b0),.io_read(rd),.io_write(wr),
 .io_address(address),.io_data(data),.io_storageData(16'hffff),.io_result(result),.io_nmi(nmi),.io_nmiEvent(retrigger),.proofStoredStatus(storedStatus),.proofStoredNmi(storedNmi){enable});
// Read-only observation of the real CPU detector, live while instruction execution is held.
BbcCpuEvents cpu(.clk(clk),.reset(reset),.enable(1'b0),.warm_reset(1'b0),.read_data(8'hea),
 .irq(1'b0),.nmi(nmi),.nmi_event(retrigger && base),.proofPreviousNmi(previous));
endmodule
"""
    return fdc + "\n" + core + "\n" + wrapper


def variants(source):
    assignments = re.findall(r"  assign immediateCompletion = [^;]+;", source)
    if len(assignments) != 1:
        raise ValueError("Changed zero-time completion anchor")
    return [
        ("positive", source),
        (
            "queued-dispatch",
            replace_once(source, assignments[0], "  assign immediateCompletion = 1'b0;"),
        ),
        (
            "missing-command-edge",
            replace_once(
                source,
                "assign io_nmi = (((eventNmi || commandNmi) && (! io_warmReset)) && (! nmiGap));",
                "assign io_nmi = ((eventNmi && (! io_warmReset)) && (! nmiGap));",
            ),
        ),
    ]


def qualify():
    current_machine.audit()
    clock_paths = [current_machine.DIRECTORY / f"clock{m}/result.json" for m in (1, 3)]
    for (multiplier, path, _), clock_path in zip(
        current_machine.parents(), clock_paths, strict=True
    ):
        clock = memory_composition.sealed(clock_path)
        if (
            clock.get("clock_multiplier") != multiplier
            or clock.get("negative_control_detected") is not True
            or clock["inputs"].get(str(path.relative_to(ROOT)))
            != hashes([path])[str(path.relative_to(ROOT))]
        ):
            raise ValueError("Zero-time command clock evidence binds a different parent")
    DIRECTORY.mkdir(parents=True, exist_ok=True)
    reference = source_root("jsbeeb")
    helper = Path(__file__).with_suffix(".mjs")
    drive_helper = ROOT / "tools/bbc/reference_drive.mjs"
    disc = ROOT / "build/bbc/disc.ssd"
    files = [
        Path(__file__),
        helper,
        drive_helper,
        disc,
        ROOT / "sim/native/bbc_zero_dispatch_test.cpp",
        ROOT / "rtl/bbc/Intel8271.scala",
        ROOT / "rtl/bbc/BbcCpu.v",
        ROOT / "build/bbc/ArletCpuEnabled.v",
        *cpu_events.files(),
        current_machine.DIRECTORY / "generation.json",
        *clock_paths,
        *[path for _, path, _ in current_machine.parents()],
        *[blob for _, path, _ in current_machine.parents() for blob in path.parent.glob("*.bin")],
        *sorted((reference / "src").rglob("*.js")),
        *[
            Path(module.__file__)
            for module in (
                callback_order,
                current_machine,
                bus_binding_proof,
                cpu_interrupt_proof,
                memory_composition,
            )
        ],
        Path(__file__).parents[1] / "video_cursor.py",
        ROOT / "tools/validation/common.py",
        *[Path(shutil.which(name)) for name in ("node", "verilator", "c++")],
    ]
    before = hashes(files)
    subprocess.run(
        ["node", str(helper), str(reference), str(drive_helper), str(disc), str(DIRECTORY)],
        check=True,
        timeout=60,
    )
    original = json.loads((DIRECTORY / "original.json").read_text())
    if original.get("passed") is not True or len(original["cases"]) != 18:
        raise ValueError("Incomplete zero-time command inventory")
    observations = sum(case["rows"] for case in original["cases"])
    artifacts = [DIRECTORY / name for name in ("original.json", "original.tsv")]
    checks = []
    for multiplier, path, top in current_machine.parents():
        for name, body in variants(path.read_text()):
            case = DIRECTORY / f"{name}{multiplier}"
            case.mkdir(exist_ok=True)
            model = case / "actual.v"
            model.write_text(
                re.sub(
                    r'\$readmemb\("([^\"]+)"',
                    lambda m: '$readmemb("' + str(path.parent / m[1]) + '"',
                    native_model(body, top),
                )
            )
            binary = callback_order.compile_native(
                model, case, "DispatchProbe", ROOT / "sim/native/bbc_zero_dispatch_test.cpp"
            )
            trial = subprocess.run(
                [str(binary), str(DIRECTORY / "original.tsv"), str(multiplier)],
                capture_output=True,
                text=True,
                timeout=120,
            )
            log = case / "run.log"
            log.write_text(trial.stdout + trial.stderr)
            accepted = (
                trial.returncode == 0
                and trial.stdout
                == f"Zero dispatch: 18 cold cases; {observations} observations; 0 differences\n"
                if name == "positive"
                else trial.returncode == 1 and trial.stdout.startswith("DISPATCH_MISMATCH ")
            )
            checks.append({"clock_multiplier": multiplier, "name": name, "accepted": accepted})
            artifacts.extend([model, binary, log, case / "build.json", case / "build.log"])
            print(
                f"Zero dispatch {multiplier} {name}: " + ("PASS" if accepted else "FAIL"),
                flush=True,
            )
            if not accepted:
                raise ValueError("Original zero-time command boundary differs: " + trial.stdout)
    current_machine.audit()
    for clock_path in clock_paths:
        memory_composition.sealed(clock_path)
    stable = before == hashes(files)
    write_json(
        DIRECTORY / "result.json",
        {
            "passed": stable,
            "inputs_unchanged": stable,
            "inputs": before,
            "artifacts": hashes(artifacts),
            "checks": checks,
            "source_cases": 18,
            "observations_per_clock": observations,
            "scope": "Public cold original FDC commands, generated actual controllers and actual CPU NMI previous-level register at 32/96 MHz. Zero-time not-ready and ready SEEK completions, repeated completions, one/three seek steps with zero settle, positive load wait and public register readbacks. Exact accepted-write NMI and cumulative physical edge counts match; no DUT state injection. Queued dispatch and missing command-edge faults must fail at both rates.",
            "limits": "Finite selected command histories; CPU held without instruction advances. Not all device histories, warm resets, instruction timing, routed timing or universal game equivalence.",
            "universal_game_equivalence": False,
        },
    )
    if not stable:
        raise ValueError("Zero-time command proof inputs changed")


if __name__ == "__main__":
    qualify()
