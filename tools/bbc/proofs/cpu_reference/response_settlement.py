"""Prove current CPU responses settle before the first following request slot."""

import json
import re
import shutil
import subprocess
from pathlib import Path

from tools.bbc import bus_binding_proof
from tools.bbc.proofs import current_machine
from tools.bbc.proofs.cpu_reference import event_composition, parent_schedule
from tools.bbc.proofs.video_cursor import hashes, replace_once
from tools.validation.common import ROOT, write_json

DIRECTORY = ROOT / "build/bbc/cpu-response-settlement"


def observed(source, top, multiplier):
    if multiplier not in (1, 3):
        raise ValueError("Unsupported response-settlement master clock")
    parent = bus_binding_proof.module(source, top)
    cpu = bus_binding_proof.ports(parent, "BbcCpu", "cpu")
    ram = bus_binding_proof.module(source, "BbcRam")
    phase = re.search(r"assign tickCpu = \(clock32 && \((\w+)\[3 : 0\] == 4'b1111\)\);", parent)
    if phase is None:
        raise ValueError("Changed actual parent CPU slot phase")
    if cpu.get("enable") != "cpuEnable" or cpu.get("clk") != "clk" or cpu.get("reset") != "reset":
        raise ValueError("Changed actual CPU response endpoint")
    # Erasing address transients before the first slot is valid only when
    # the actual wait latch cannot sample them between slot boundaries.
    if (
        len(re.findall(r"\bdelayedSlow\s*<=", parent)) != 2
        or parent.count("delayedSlow <= 1'b0;") != 1
        or parent.count(
            "if(tickCpu) begin\n        delayedSlow <= (decode_io_slow && (! cpuEnable));"
        )
        != 1
    ):
        raise ValueError("Slow-access latch no longer updates only at request slots")
    ram = replace_once(
        ram, "module BbcRam (", "module BbcRam (output [7:0] proofData,output proofCapture,"
    )
    ram = replace_once(
        ram,
        "endmodule",
        "assign proofData=data;assign proofCapture=io_cpuEnable_regNext;\nendmodule",
    )
    parent = replace_once(
        parent,
        "BbcRam ram (",
        "BbcRam ram (.proofData(proofRamByte),.proofCapture(proofRamCapture),",
    )
    # Abstract only the array's raw byte. The existing clock/enable and
    # response-holding registers remain the actual generated implementation.
    parent = replace_once(
        parent, "rom_spinal_port0 <= rom[romAddress];", "rom_spinal_port0 <= proofRomByte;"
    )
    children = [
        instance
        for kind, instance in re.findall(r"^\s+(\w+)\s+(\w+)\s*\(\s*$", parent, re.M)
        if kind not in {"BbcRam", "BbcAddressDecode"}
    ]
    observation = f"""
(* anyseq *) reg [7:0] proofRomByte;
wire [7:0] proofRamByte;
wire proofRamCapture;
wire [7:0] proofRead={cpu["read_data"]};
reg [1:0] recent=0;
reg [7:0] previousRead=0;
reg previousRam=0;
reg [7:0] expectedRam=0,expectedPeripheral=8'hff;
reg [7:0] expectedRom=0;
reg [1:0] expectedSelect=0;
reg expectedPresent=1;
reg romSeen=0;
always @(posedge clk)if(cpuEnable)expectedRom<=proofRomByte;
always @(posedge clk or posedge reset)
 if(reset)begin
  recent<=0;previousRead<=0;previousRam<=0;
  expectedRam<=0;expectedPeripheral<=8'hff;expectedSelect<=0;expectedPresent<=1;
  romSeen<=0;
 end else begin
  recent<={{recent[0],cpuEnable}};
  previousRead<=proofRead;
  previousRam<=cpuEnable && cpu_address<16'h8000;
  if(previousRam)expectedRam<=proofRamByte;
  if(cpuEnable)begin
   romSeen<=1;
   expectedPeripheral<=peripheral;
   expectedSelect<=cpu_address<16'h8000 ? 2'd0:
    cpu_address>=16'hfc00 && cpu_address<16'hff00 ? 2'd2:2'd1;
   expectedPresent<=cpu_address>=16'hc000 || romSelect>=14;
  end
 end
wire [7:0] expectedRead=expectedSelect==0 ? expectedRam:
 expectedSelect==1 ? (expectedPresent ? expectedRom:8'd0):expectedPeripheral;
initial assume(reset);
wire proofTransport=proofRead==expectedRead;
wire proofRetention=(|recent) || proofRead==previousRead;
wire proofFirstSlot=!(tickCpu && |recent);
wire proofAcceptSlot=!cpuEnable || tickCpu;
wire proofBuffers=ram_io_cpuReadData==expectedRam && peripheralData==expectedPeripheral &&
 (!romSeen || romData==expectedRom);
wire proofSelection=memorySelect==expectedSelect && romPresent==expectedPresent &&
 (expectedSelect!=1 || romSeen);
wire proofInvariant=proofRamCapture==previousRam &&
 (!recent[0] || {phase[1]}[3:0]==0) &&
 (!recent[1] || {phase[1]}[3:0]==4'd{1 if multiplier == 1 else 0});
wire proofObservation=reset || (proofTransport && proofRetention && proofFirstSlot && proofAcceptSlot);
wire proofEquivalent=proofObservation && (reset || (proofInvariant && proofBuffers && proofSelection));
"""
    original_parent = bus_binding_proof.module(source, top)
    return (
        replace_once(
            replace_once(source, bus_binding_proof.module(source, "BbcRam"), ram),
            original_parent,
            parent.removesuffix("endmodule") + observation + "endmodule",
        ),
        children,
    )


def guard_domain(graph, top):
    module = graph["modules"][top]
    assumptions = [cell for cell in module["cells"].values() if cell["type"] == "$assume"]
    initial = [cell for cell in module["cells"].values() if cell["type"] == "$initstate"]
    if (
        len(assumptions) != 1
        or len(initial) != 1
        or assumptions[0]["connections"]["A"] != module["ports"]["reset"]["bits"]
        or assumptions[0]["connections"]["EN"] != initial[0]["connections"]["Y"]
        or any(cell["type"] == "$check" for cell in module["cells"].values())
    ):
        raise ValueError("Response proof assumptions exceed initial reset")
    used = set()
    for name, width in (
        ("cpu_address", 16),
        ("cpu_write_enable", 1),
        ("cpu_write_data", 8),
        ("peripheral", 8),
        ("proofRomByte", 8),
        ("proofRamByte", 8),
    ):
        wire = module["netnames"][name]
        bits = wire["bits"]
        if (
            len(bits) != width
            or any(type(bit) is not int for bit in bits)
            or used.intersection(bits)
            or "init" in wire["attributes"]
            or len(
                [
                    cell
                    for cell in module["cells"].values()
                    if cell["type"] == "$anyseq" and cell["connections"]["Y"] == bits
                ]
            )
            != 1
        ):
            raise ValueError("Response proof narrowed or aliased its input: " + name)
        used.update(bits)


def qualify():
    current_machine.audit()
    premise = parent_schedule.DIRECTORY / "result.json"
    tree = event_composition.audit_tree([premise])
    DIRECTORY.mkdir(parents=True, exist_ok=True)
    files = [
        Path(__file__),
        *tree,
        current_machine.DIRECTORY / "generation.json",
        *[path for _, path, _ in current_machine.parents()],
        ROOT / "rtl/bbc/BbcMachine.scala",
        ROOT / "rtl/bbc/BbcRam.scala",
        Path(current_machine.__file__),
        Path(bus_binding_proof.__file__),
        Path(event_composition.__file__),
        Path(parent_schedule.__file__),
        Path(__file__).parents[1] / "video_cursor.py",
        ROOT / "tools/validation/common.py",
        Path(shutil.which("yosys")),
    ]
    before = hashes(files)
    checks, artifacts = [], []
    for multiplier, path, top in current_machine.parents():
        source = path.read_text()
        variants = (
            ("positive", source),
            (
                "lost-ram-capture",
                replace_once(
                    source,
                    "if(io_cpuEnable_regNext) begin\n        response <= data;",
                    "if(1'b0) begin\n        response <= data;",
                ),
            ),
            (
                "transparent-rom",
                replace_once(
                    source,
                    "if(cpuEnable) begin\n      rom_spinal_port0 <= rom[romAddress];",
                    "if(1'b1) begin\n      rom_spinal_port0 <= rom[romAddress];",
                ),
            ),
            ("transparent-peripheral", source),
        )
        for name, text in variants:
            # This mutation changes the real accepted peripheral capture to
            # a free-running latch, without touching the shadow observer.
            if name == "transparent-peripheral":
                text = replace_once(
                    text,
                    "if(cpuEnable) begin\n        peripheralData <= peripheral;",
                    "if(1'b1) begin\n        peripheralData <= peripheral;",
                )
            model_text, children = observed(text, top, multiplier)
            case = DIRECTORY / f"{name}{multiplier}"
            case.mkdir(exist_ok=True)
            model, graph, log, witness = [
                case / filename
                for filename in ("actual.v", "graph.json", "proof.log", "witness.json")
            ]
            model.write_text(model_text)
            witness.unlink(missing_ok=True)
            command = (
                f"read_verilog -formal -D SYNTHESIS {model}; delete "
                + " ".join(f"{top}/c:{child}" for child in children)
                + f"; hierarchy -top {top}; proc; delete -output {top}/o:io_*; "
                + f"cutpoint {top}/w:cpu_address {top}/w:cpu_write_enable {top}/w:cpu_write_data {top}/w:peripheral BbcRam/w:data; "
                + f"expose {top}/w:proofEquivalent {top}/w:proofObservation; opt_clean -purge; flatten; async2sync; opt -full; check -assert; write_json {graph}; "
            )
            positive = name == "positive"
            property_name = "proofEquivalent" if positive else "proofObservation"
            horizon = (
                "-tempinduct -initsteps 3 -maxsteps 12" if positive else f"-seq {20 * multiplier}"
            )
            command += f"sat -set-assumes -set-init-zero {horizon} -prove {property_name} 1 -verify -timeout 90 -show {property_name} -show proofTransport -show proofRetention -show proofFirstSlot -show proofInvariant -show cpu_address -dump_json {witness}"
            with log.open("w") as output:
                trial = subprocess.run(
                    ["yosys", "-Q", "-T", "-p", command],
                    cwd=path.parent,
                    stdout=output,
                    stderr=subprocess.STDOUT,
                    timeout=180,
                )
            log_text = log.read_text()
            if graph.is_file():
                guard_domain(json.loads(graph.read_text()), top)
            accepted = (
                trial.returncode == 0 and "Induction step proven: SUCCESS!" in log_text
                if positive
                else trial.returncode != 0
                and "ERROR: Called with -verify and proof did fail!" in log_text
                and witness.is_file()
                and any(
                    signal["name"] == property_name and "0" in signal.get("wave", "")
                    for signal in json.loads(witness.read_text())["signal"]
                )
            )
            checks.append({"clock_multiplier": multiplier, "name": name, "accepted": accepted})
            artifacts.extend([model, graph, log])
            if witness.is_file():
                artifacts.append(witness)
            print(
                f"CPU response settlement {multiplier} {name}: " + ("PASS" if accepted else "FAIL"),
                flush=True,
            )
            if not accepted:
                raise ValueError("CPU response settlement proof incomplete")
    current_machine.audit()
    event_composition.audit_tree([premise])
    stable = before == hashes(files)
    report = {
        "passed": stable,
        "inputs_unchanged": stable,
        "inputs": before,
        "artifacts": hashes(artifacts),
        "checks": checks,
        "scope": "Initialized unbounded actual-parent response transport, settlement and retention at both master rates. The actual RAM response capture, synchronous ROM enable, peripheral latch and registered response selector remain executing. Raw memory and peripheral bytes and CPU addresses/writes are unrestricted. Every selected response equals the declared registered transport model; it holds after two master edges without acceptance, and no following 2 MHz request slot occurs inside that settlement window. Only initial reset is assumed. Actual RAM capture, ROM enable and peripheral retention faults require observable counterexamples.",
        "composition": "The current source/parent scheduler receipt binds the same two generated parents. Response settlement precedes every first following request slot; response retention then supplies its actual-CPU stable-data address-hold premise until acceptance. Changes before that first slot cannot affect delayedSlow, which is updated only on tickCpu. Therefore the held-request timing interface is discharged for the actual registered response transport, without assuming CPU addresses stay fixed during settlement.",
        "limits": "Transport and timing, not correctness of raw RAM/ROM/peripheral byte values or device-generated IRQ/NMI. Declared shared-reset relative epoch, no later CPU warm/cold restart in the consumed CPU-history composition. Device callback ordering, instruction eligibility, absolute source epoch, video and whole-machine/game equivalence remain separate obligations.",
        "universal_game_equivalence": False,
    }
    write_json(DIRECTORY / "result.json", report)
    if not stable:
        raise ValueError("Response settlement inputs changed")
    return report


if __name__ == "__main__":
    qualify()
