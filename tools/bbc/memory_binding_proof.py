"""Prove actual CPU memory routing and registered response selection."""

import re
import subprocess
from pathlib import Path

from tools.validation.common import ROOT, write_json

from . import build, bus_binding_proof, video96


def observed(text, top):
    instrumented, serial = bus_binding_proof.observed(text, top)
    parent = bus_binding_proof.module(instrumented, top)
    ram = bus_binding_proof.ports(parent, "BbcRam", "ram")
    cpu = bus_binding_proof.ports(parent, "BbcCpu", "cpu")
    if not (
        {"io_cpuReadData", "io_cpuEnable", "io_cpuAddress", "io_cpuWrite", "io_cpuData"}.issubset(
            ram
        )
        and {"read_data", "enable", "clk", "reset"}.issubset(cpu)
    ):
        raise ValueError("Missing actual CPU/RAM memory-binding ports")
    for name, required in {"clk": "clk", "reset": "reset"}.items():
        if any(re.sub(r"\s+", "", ports.get(name, "")) != required for ports in (ram, cpu)):
            raise ValueError("Changed CPU/RAM memory-binding clock/reset")
    if re.sub(r"\s+", "", cpu.get("enable", "")) != "cpuEnable":
        raise ValueError("Changed actual CPU accepted-enable binding")
    for name, pattern in {
        "romSelect": r"reg\s+\[3:0\]\s+romSelect\s*;",
        "romPresent": r"reg\s+romPresent\s*;",
        "peripheralData": r"reg\s+\[7:0\]\s+peripheralData\s*;",
        "memorySelect": r"reg\s+\[1:0\]\s+memorySelect\s*;",
    }.items():
        if len(re.findall(pattern, parent)) != 1:
            raise ValueError("Changed actual memory-selection state: " + name)
    addition = """
reg [3:0] expectedBank=0;
reg [1:0] expectedSelect=0;
reg expectedPresent=1;
reg [7:0] expectedPeripheral=8'hff;
initial assume(reset);
always @(posedge clk or posedge reset)
 if(reset) begin
  expectedBank<=0;expectedSelect<=0;expectedPresent<=1;expectedPeripheral<=8'hff;
 end else begin
  if(cpuEnable) begin
   expectedSelect<=address<16'h8000 ? 2'd0 :
                   address>=16'hfc00 && address<16'hff00 ? 2'd2 : 2'd1;
   expectedPresent<=address>=16'hc000 || expectedBank>=14;
   expectedPeripheral<=expectedData;
  end
  if(acceptedWrite && ((address>=16'hfe30 && address<=16'hfe37) ||
                      (address>=16'hfe3c && address<=16'hfe3f)))
   expectedBank<=cpu_write_data[3:0];
 end
wire [15:0] expectedRomAddress={2'b00,address[13:0]} +
 (address<16'hc000 ? (expectedBank==15 ? 16'h4000 : 16'h8000) : 16'h0000);
wire [7:0] expectedRead=expectedSelect==0 ? RAM_RESULT :
 expectedSelect==1 ? (expectedPresent ? romData : 8'h00) : expectedPeripheral;
wire proofMemoryEquivalent=proofEquivalent &&
 romSelect==expectedBank && memorySelect==expectedSelect &&
 romPresent==expectedPresent && peripheralData==expectedPeripheral &&
 romAddress==expectedRomAddress && CPU_READ==expectedRead &&
 RAM_ENABLE==(cpuEnable && address<16'h8000) &&
 RAM_ADDRESS==address[14:0] && RAM_WRITE==cpu_write_enable && RAM_DATA==cpu_write_data;
"""
    replacements = {
        "RAM_RESULT": ram["io_cpuReadData"],
        "CPU_READ": cpu["read_data"],
        "RAM_ENABLE": ram["io_cpuEnable"],
        "RAM_ADDRESS": ram["io_cpuAddress"],
        "RAM_WRITE": ram["io_cpuWrite"],
        "RAM_DATA": ram["io_cpuData"],
    }
    for name, expression in replacements.items():
        addition = addition.replace(name, "(" + expression + ")")
    return instrumented.replace(
        parent, parent.removesuffix("endmodule") + addition + "endmodule", 1
    ), serial


def qualify(*, sources=None, directory=None):
    supplied = sources is not None
    if not supplied:
        if not build.generation_current():
            raise ValueError("Generate the actual whole machine before proving memory routing")
        video96.generate()
    sources = sources or [
        (1, build.BBC / "BbcMachine.v", "BbcMachine"),
        (3, video96.DIRECTORY / "BbcMachine96.v", "BbcMachine96"),
    ]
    dependencies = [
        Path(__file__),
        Path(bus_binding_proof.__file__),
        ROOT / "rtl/bbc/BbcCpu.v",
        *[p for _, p, _ in sources],
    ]
    before = build.hashes(dependencies)
    directory = directory or build.BBC / "memory-binding-proof"
    directory.mkdir(exist_ok=True)
    clocks = []
    for multiplier, source, top in sources:
        original = source.read_text()
        instrumented, serial = observed(original, top)
        parent = bus_binding_proof.module(original, top)
        children = re.findall(r"^\s+\w+\s+(\w+)\s*\(\s*$", parent, re.M)
        children = [name for name in children if name != "decode"]
        cuts = [
            "cpu_address",
            "cpu_write_enable",
            "cpu_write_data",
            "cpuEnable",
            "tickCpu",
            "sys_io_irq",
            "user_io_irq",
            "serial_io_irq",
            "serial_io_result",
            "sys_io_result",
            "user_io_result",
            "adc_io_result",
            "video_io_result",
            serial["proof_fdc_result"],
            "ram_io_cpuReadData",
            "romData",
        ]

        def prove(text, name):
            model = directory / (name + str(multiplier) + ".v")
            model.write_text(text)
            command = (
                f"read_verilog -formal {model} {ROOT / 'rtl/bbc/BbcCpu.v'}; hierarchy -top {top}; "
                + "delete "
                + " ".join(f"{top}/c:{child}" for child in children)
                + "; "
                + f"hierarchy -top {top}; proc; delete -output {top}/o:io_*; "
                + "cutpoint "
                + " ".join(f"{top}/w:{cut}" for cut in cuts)
                + "; "
                + f"expose {top}/w:proofMemoryEquivalent; opt_clean -purge; flatten; async2sync; "
                + "sat -set-assumes -set-init-zero -tempinduct -seq 3 -maxsteps 8 "
                + "-prove proofMemoryEquivalent 1 -verify -timeout 90"
            )
            result = subprocess.run(
                ["yosys", "-Q", "-T", "-p", command],
                cwd=source.parent,
                capture_output=True,
                text=True,
                timeout=120,
            )
            (directory / (name + str(multiplier) + ".log")).write_text(
                result.stdout + result.stderr
            )
            return result

        positive = prove(instrumented, "proof")
        negatives = []
        for name, correct, wrong in (
            (
                "wrong-peripheral-hold",
                "if(cpuEnable) begin\n        peripheralData <= peripheral;",
                "if(1'b1) begin\n        peripheralData <= peripheral;",
            ),
            (
                "wrong-ram-enable",
                "assign ram_io_cpuEnable = (cpuEnable && (cpu_address < 16'h8000));",
                "assign ram_io_cpuEnable = cpuEnable;",
            ),
        ):
            if original.count(correct) != 1:
                raise ValueError("Missing actual memory mutation target: " + name)
            result = prove(observed(original.replace(correct, wrong), top)[0], name)
            negatives.append(
                {
                    "name": name,
                    "detected": result.returncode != 0 and "proof did fail" in result.stderr,
                }
            )
        clocks.append(
            {
                "clock_multiplier": multiplier,
                "passed": positive.returncode == 0,
                "negative_controls": negatives,
            }
        )
    stable = before == build.hashes(dependencies) and (supplied or build.generation_current())
    report = {
        "passed": stable
        and all(c["passed"] and all(n["detected"] for n in c["negative_controls"]) for c in clocks),
        "inputs": before,
        "inputs_unchanged": stable,
        "root_generation_bound": not supplied,
        "clocks": clocks,
        "artifacts": build.hashes([*directory.glob("*.v"), *directory.glob("*.log")]),
        "scope": "Actual generated parents at both master clocks. Arbitrary CPU requests, accepted enables and child data/IRQ outputs after reset: registered RAM/ROM/peripheral selection, held peripheral bytes, ROM bank writes/presence/address, RAM enable/address/write/data and the actual CPU read-data connection. Joint independent reference latch invariants and every existing peripheral decoder/IRQ property are proved inductively; two real timing/routing faults must fail per clock.",
        "limits": "Local parent routing/retention proof. CPU values/enables and RAM/ROM/device read bytes are independent cutpoints; it does not prove CPU scheduling, the contents or latency of ROM/RAM themselves, device semantics or universal whole-game equivalence. Separate RAM, clock and device proofs discharge their stated local contracts.",
    }
    write_json(directory / "result.json", report)
    print(f"Actual memory routing proof: {report['passed']}", flush=True)
    if not report["passed"]:
        raise ValueError("Actual memory routing proof failed; private models and logs retained")
    return report


if __name__ == "__main__":
    qualify()
