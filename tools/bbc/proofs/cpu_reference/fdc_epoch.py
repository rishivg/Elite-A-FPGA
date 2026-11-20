"""Actual parent epochs and FDC acknowledgement-gap retention against original commands."""

import json
import re
import subprocess
from pathlib import Path

from tools.bbc import bus_binding_proof, cpu_events
from tools.bbc.proofs import current_machine
from tools.bbc.proofs.cpu_reference import callback_order
from tools.bbc.proofs.video_cursor import replace_once
from tools.validation.common import ROOT, source_root

DIRECTORY = ROOT / "build/bbc/fdc-source-epoch"


def binding(source, top):
    parent = bus_binding_proof.module(source, top)
    controller, extra = callback_order.fdc_instance(source, top)
    required = (
        "assign io_fdcRead = (read && decode_io_fdc);",
        "assign io_fdcWrite = (write && decode_io_fdc);",
        "assign read = (cpuEnable && (! cpu_write_enable));",
        "assign write = (cpuEnable && cpu_write_enable);",
        "assign io_fdcAddress = cpu_address[2 : 0];",
    )
    if any(parent.count(statement) != 1 for statement in required):
        raise ValueError("Changed actual FDC accepted-access binding")
    fdc_name = re.findall(r"\bIntel8271\s+(\w+)\s*\(", parent)[0]
    ports = bus_binding_proof.ports(parent, "Intel8271", fdc_name)
    for name, signal in (
        ("io_read", "io_fdcRead"),
        ("io_write", "io_fdcWrite"),
        ("io_address", "io_fdcAddress[2:0]"),
        ("io_data", "cpu_write_data[7:0]"),
    ):
        if ports[name].strip() != signal:
            raise ValueError("FDC access bypasses actual parent: " + name)
    return parent, controller, extra


def native_model(source, top):
    parent, _, _ = binding(source, top)
    event = bus_binding_proof.ports(parent, "BbcCpu", "cpu")["nmi_event"]
    fdc_name = re.findall(r"\bIntel8271\s+(\w+)\s*\(", parent)[0]
    fdc_ports = bus_binding_proof.ports(parent, "Intel8271", fdc_name)
    blocks = list(re.finditer(r"^  (\w+) (\w+) \(\n.*?^  \);", parent, re.M | re.S))
    if not {"cpu", "decode", fdc_name}.issubset({match[2] for match in blocks}):
        raise ValueError("Changed actual parent instance inventory")
    # Keep the real divider, phase, slow wait, address decoder, strobes and FDC.
    # A public held-request fixture replaces instruction execution; unrelated
    # device outputs are unused in this probe, not claimed equivalent.
    for match in reversed(blocks):
        if match[2] not in {"decode", fdc_name}:
            parent = parent[: match.start()] + parent[match.end() :]
    parent = replace_once(
        parent,
        f"module {top} (",
        f"module {top} (input[15:0] proofAddress,input[7:0] proofData,input proofWrite,output proofSlot,output proofAccepted,output[7:0] proofResult,output proofNmi,output proofStored,output proofEvent,",
    )
    parent = replace_once(
        parent,
        "endmodule",
        """
assign cpu_address=proofAddress;assign cpu_write_data=proofData;assign cpu_write_enable=proofWrite;
assign proofSlot=tickCpu;assign proofAccepted=cpuEnable;
assign proofResult=PROOF_RESULT;assign proofNmi=PROOF_NMI;
assign proofStored=PROOF_FDC.nmiLevel;
assign proofEvent=PROOF_EVENT;
endmodule""",
    )
    parent = (
        parent.replace("PROOF_RESULT", fdc_ports["io_result"])
        .replace("PROOF_NMI", fdc_ports["io_nmi"])
        .replace("PROOF_FDC", fdc_name)
        .replace("PROOF_EVENT", event)
    )
    original = bus_binding_proof.module(source, top)
    source = replace_once(source, original, parent)
    source = cpu_events.strip(source)
    core = cpu_events.observed({"PreviousNmi": ("", "NMI_1")})
    wrapper = f"""module DeviceEpochProbe(input clk,reset,input[15:0]address,input[7:0]data,
input write,input[15:0]storage,output[17:0]storageAddress,output storageWrite,
output[15:0]storageData,output slot,accepted,fdcRead,fdcWrite,output[7:0]result,
output nmi,previous,level,eventPulse);
{top} parent(.clk(clk),.reset(reset),.io_warmReset(1'b0),.io_storageData(storage),
 .proofAddress(address),.proofData(data),.proofWrite(write),.proofSlot(slot),
 .proofAccepted(accepted),.proofResult(result),.proofNmi(nmi),.proofStored(level),.proofEvent(eventPulse),
 .io_fdcRead(fdcRead),.io_fdcWrite(fdcWrite),.io_storageAddress(storageAddress),
 .io_storageWrite(storageWrite),.io_storageWriteData(storageData));
BbcCpuEvents observer(.clk(clk),.reset(reset),.enable(1'b0),.warm_reset(1'b0),
 .read_data(8'hea),.irq(1'b0),.nmi(nmi),.nmi_event(eventPulse),.proofPreviousNmi(previous));
endmodule
"""
    return source + "\n" + core + "\n" + wrapper


def run_native():
    DIRECTORY.mkdir(parents=True, exist_ok=True)
    reference = source_root("jsbeeb")
    helper = Path(__file__).with_suffix(".mjs")
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
    if original.get("passed") is not True or len(original["cases"]) != 7:
        raise ValueError("Incomplete original epoch inventory")
    observations = sum(case["rows"] for case in original["cases"])
    fault = DIRECTORY / "fault.tsv"
    fault.write_text(fault_fixture((DIRECTORY / "original.tsv").read_text()))
    artifacts = [DIRECTORY / name for name in ("original.json", "original.tsv", "fault.tsv")]
    checks = []
    for multiplier, path, top in current_machine.parents():
        source = path.read_text()
        parent = bus_binding_proof.module(source, top)
        phase = re.search(r"assign tickCpu = \(clock32 && \((\w+)\[3 : 0\] == 4'b1111\)\);", parent)
        if phase is None:
            raise ValueError("Changed actual parent phase")
        pin = "assign io_nmi = (((eventNmi || commandNmi) && (! io_warmReset)) && (! nmiGap));"
        for name, body in (
            ("positive", source),
            (
                "missing-gap",
                replace_once(
                    source,
                    pin,
                    "assign io_nmi = ((eventNmi || commandNmi) && (! io_warmReset));",
                ),
            ),
            (
                "shifted-parent-epoch",
                replace_once(source, f"{phase[1]} <= 5'h0;", f"{phase[1]} <= 5'h1;"),
            ),
        ):
            case = DIRECTORY / f"{name}{multiplier}"
            case.mkdir(exist_ok=True)
            model = case / "actual.v"
            model.write_text(
                re.sub(
                    r'\$readmemb\("([^\"]+)"',
                    lambda match: '$readmemb("' + str(path.parent / match[1]) + '"',
                    native_model(body, top),
                )
            )
            binary = callback_order.compile_native(
                model,
                case,
                "DeviceEpochProbe",
                ROOT / "sim/native/bbc_fdc_branch_test.cpp",
                dependencies=[
                    ROOT / "sim/native/bbc_disc_payload.h",
                    ROOT / "sim/native/bbc_fm_media.h",
                ],
            )
            trial = subprocess.run(
                [
                    str(binary),
                    str(DIRECTORY / "original.tsv") if name == "positive" else str(fault),
                    str(ROOT / "build/bbc/disc.ssd"),
                    str(multiplier),
                    "7" if name == "positive" else "1",
                ],
                capture_output=True,
                text=True,
                timeout=360,
            )
            log = case / "run.log"
            log.write_text(trial.stdout + trial.stderr)
            accepted = (
                trial.returncode == 0
                and trial.stdout
                == f"FDC epoch: 7 cold cases; {observations} observations; 0 differences\n"
                if name == "positive"
                else trial.returncode == 1
                and trial.stdout.startswith(
                    "FDC_EPOCH_MISMATCH last-slot-read "
                    if name == "missing-gap"
                    else "EPOCH_MISMATCH last-slot-read "
                )
            )
            checks.append({"clock_multiplier": multiplier, "name": name, "accepted": accepted})
            artifacts.extend([model, binary, log, case / "build.json", case / "build.log"])
            print(
                f"FDC source epoch {multiplier} {name}: " + ("PASS" if accepted else "FAIL"),
                flush=True,
            )
            if not accepted:
                raise ValueError("FDC parent/source epoch differs: " + trial.stdout)
    return checks, artifacts


def fault_fixture(text):
    """Select the complete late-service case; malformed controls cannot pass."""
    lines = text.splitlines()
    cursor, names, selected = 0, [], None
    while cursor < len(lines):
        header = lines[cursor].split()
        if len(header) != 4 or header[0] != "case" or not header[3].isdigit():
            raise ValueError("Malformed epoch fixture header")
        end = cursor + 1 + int(header[3])
        if end > len(lines) or any(len(line.split()) != 7 for line in lines[cursor + 1 : end]):
            raise ValueError("Truncated epoch fixture")
        names.append(header[2])
        if header[2] == "last-slot-read":
            if selected is not None:
                raise ValueError("Duplicate last-slot epoch fixture")
            selected = lines[cursor:end]
            selected[0] = "case 0 last-slot-read " + header[3]
        cursor = end
    if len(names) != 7 or len(set(names)) != 7 or selected is None:
        raise ValueError("Incomplete epoch fixture inventory")
    return "\n".join(selected) + "\n"


if __name__ == "__main__":
    run_native()
