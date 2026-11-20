"""Prove actual parent device strobes, read mux and serial interrupt wiring."""

import re
import subprocess
from pathlib import Path

from tools.validation.common import ROOT, write_json

from . import build, bus_decode, video96


def module(text, name):
    matches = re.findall(r"^module\s+" + re.escape(name) + r"\b[\s\S]*?^endmodule\b", text, re.M)
    if len(matches) != 1:
        raise ValueError("Missing or duplicate actual module: " + name)
    return matches[0]


def ports(parent, kind, instance):
    kinds = r"(?:BbcCpu|BbcCpuEvents)" if kind == "BbcCpu" else re.escape(kind)
    children = re.findall(
        r"\b" + kinds + r"\s+" + re.escape(instance) + r"\s*\(([\s\S]*?)\n\s*\);", parent
    )
    if len(children) != 1:
        raise ValueError("Missing actual child: " + instance)
    return {
        name: expression.strip()
        for name, expression in re.findall(r"\.(\w+)\s*\(([^()]*)\)", children[0])
    }


def observed(text, top):
    parent = module(text, top)
    serial = ports(parent, "BbcSerial", "serial")
    decoder = ports(parent, "BbcAddressDecode", "decode")
    cpu = ports(parent, "BbcCpu", "cpu")
    fdc_name = "intel8271_1" if re.search(r"\bIntel8271\s+intel8271_1\s*\(", parent) else "fdc"
    fdc = ports(parent, "Intel8271", fdc_name)
    for port, required in {
        "clk": "clk",
        "reset": "reset",
        "io_tick": "tickCpu",
    }.items():
        if re.sub(r"\s+", "", serial.get(port, "")) != required:
            raise ValueError("Wrong actual serial port: " + port)
    if re.sub(r"\s+", "", decoder.get("io_address", "")) != "cpu_address[15:0]":
        raise ValueError("Wrong actual decoder address")
    enables = re.findall(
        r"assign\s+(when_BbcMachine_l\d+)\s*=\s*\(write && decode_io_romSelect\);", parent
    )
    if len(enables) != 1:
        raise ValueError("Changed actual ROM selector write gate")
    expressions = [
        f"{serial['io_address']} == cpu_address[0]",
        f"{serial['io_data']} == cpu_write_data",
        f"{serial['io_aciaRead']} == (acceptedRead && aciaSelected)",
        f"{serial['io_aciaWrite']} == (acceptedWrite && aciaSelected)",
        f"{serial['io_serialRead']} == (acceptedRead && serialSelected)",
        f"{serial['io_serialWrite']} == (acceptedWrite && serialSelected)",
        f"{cpu['irq']} == (sys_io_irq || user_io_irq || serial_io_irq)",
        "video_io_crtcWrite == (acceptedWrite && address >= 16'hfe00 && address <= 16'hfe07)",
        "video_io_ulaWrite == (acceptedWrite && address >= 16'hfe20 && address <= 16'hfe2f)",
        "io_fdcRead == (acceptedRead && address >= 16'hfe80 && address <= 16'hfe9f)",
        "io_fdcWrite == (acceptedWrite && address >= 16'hfe80 && address <= 16'hfe9f)",
        "adc_io_read == (acceptedRead && address >= 16'hfec0 && address <= 16'hfedf)",
        "adc_io_write == (acceptedWrite && address >= 16'hfec0 && address <= 16'hfedf)",
        "sys_io_read == (acceptedRead && address >= 16'hfe40 && address <= 16'hfe5f)",
        "sys_io_write == (acceptedWrite && address >= 16'hfe40 && address <= 16'hfe5f)",
        "user_io_read == (acceptedRead && address >= 16'hfe60 && address <= 16'hfe7f)",
        "user_io_write == (acceptedWrite && address >= 16'hfe60 && address <= 16'hfe7f)",
        f"{enables[0]} == (acceptedWrite && ((address >= 16'hfe30 && address <= 16'hfe37) || (address >= 16'hfe3c && address <= 16'hfe3f)))",
        "peripheral == expectedData",
        "decode_io_slow == expectedSlow",
        f"!({serial['io_aciaWrite']} && {serial['io_serialWrite']})",
        f"!({serial['io_aciaRead']} && {serial['io_serialRead']})",
        f"!({serial['io_aciaWrite']} && {serial['io_serialRead']})",
        f"!({serial['io_serialRead']} && {serial['io_serialWrite']})",
    ]
    addition = (
        """
wire [15:0] address = cpu_address;
wire acceptedRead = cpuEnable && !cpu_write_enable;
wire acceptedWrite = cpuEnable && cpu_write_enable;
wire aciaSelected = address >= 16'hfe08 && address <= 16'hfe0f;
wire serialSelected = address >= 16'hfe10 && address <= 16'hfe17;
wire [7:0] unusedData = address < 16'hfe00 || (address >= 16'hfe18 && address <= 16'hfe1b) || (address >= 16'hfe20 && address <= 16'hfe23) ? 8'hff : address[15:8];
wire [7:0] expectedData =
address >= 16'hfe00 && address <= 16'hfe07 ? video_io_result :
aciaSelected ? serial_io_result : serialSelected ? 8'h00 :
address >= 16'hfe40 && address <= 16'hfe5f ? sys_io_result :
address >= 16'hfe60 && address <= 16'hfe7f ? user_io_result :
address >= 16'hfe80 && address <= 16'hfe9f ? fdc_io_result :
address >= 16'hfec0 && address <= 16'hfedf ? adc_io_result : unusedData;
wire expectedSlow = address >= 16'hfc00 && address < 16'hff00 &&
(address < 16'hfe00 || address[7:5] == 0 || address[7:5] == 2 || address[7:5] == 3 || address[7:5] == 6);
wire proofEquivalent;
assign proofEquivalent = """
        + " &&\n".join("(" + expression + ")" for expression in expressions)
        + ";\n"
    )
    addition = addition.replace("fdc_io_result", fdc["io_result"])
    serial["proof_fdc_result"] = fdc["io_result"].split("[")[0]
    return text.replace(
        parent, parent.removesuffix("endmodule") + addition + "endmodule", 1
    ), serial


def qualify():
    if not build.generation_current():
        raise ValueError("Generate the actual machine first")
    bus_decode.elaborate()
    video96.generate()
    directory = build.BBC / "bus-binding-proof"
    directory.mkdir(exist_ok=True)
    sources = [
        (1, build.BBC / "BbcMachine.v", "BbcMachine"),
        (3, video96.DIRECTORY / "BbcMachine96.v", "BbcMachine96"),
    ]
    before = build.hashes(
        [
            Path(__file__),
            ROOT / "rtl/bbc/BbcCpu.v",
            *[source for _, source, _ in sources],
            bus_decode.DIRECTORY / "BbcSerial.v",
            bus_decode.DIRECTORY / "BbcAddressDecode.v",
        ]
    )
    results = []
    for multiplier, source, top in sources:
        text = source.read_text()
        for name in ("BbcSerial", "BbcAddressDecode"):
            if re.sub(r"\s+", "", module(text, name)) != re.sub(
                r"\s+", "", module((bus_decode.DIRECTORY / (name + ".v")).read_text(), name)
            ):
                raise ValueError("Parent child differs from qualified standalone class: " + name)
        actual = directory / f"actual{multiplier}.v"
        instrumented, connections = observed(text, top)
        actual.write_text(instrumented)

        def prove(model, log):
            children = "cpu ram sys user adc video systemPins fdc intel8271_1 audio serial".split()
            cuts = "cpu_address cpu_write_enable cpu_write_data cpuEnable tickCpu sys_io_irq user_io_irq serial_io_irq video_io_result sys_io_result user_io_result fdc_io_result adc_io_result serial_io_result".split()
            cuts = [
                connections["proof_fdc_result"] if name == "fdc_io_result" else name
                for name in cuts
            ]
            command = (
                f"read_verilog -formal {model} {ROOT / 'rtl/bbc/BbcCpu.v'}; hierarchy -top {top}; "
                + "delete "
                + " ".join(f"{top}/c:{child}" for child in children)
                + "; "
                + f"hierarchy -top {top}; proc; delete -output {top}/o:io_*; "
                + "expose -input "
                + " ".join(f"{top}/w:{cut}" for cut in cuts)
                + "; "
                + f"expose {top}/w:proofEquivalent; flatten; opt_clean -purge; "
                + "sat -set-def-inputs -prove proofEquivalent 1 -verify"
            )
            with log.open("w") as output:
                result = subprocess.run(
                    ["yosys", "-Q", "-T", "-p", command],
                    cwd=source.parent,
                    stdout=output,
                    stderr=subprocess.STDOUT,
                    timeout=120,
                )
            return result.returncode, log.read_text()

        code, log = prove(actual, directory / f"proof{multiplier}.log")
        target = "assign cpu_irq = ((sys_io_irq || user_io_irq) || serial_io_irq);"
        if instrumented.count(target) != 1:
            raise ValueError("Missing actual IRQ negative target")
        negative = directory / f"wrong-irq{multiplier}.v"
        negative.write_text(
            instrumented.replace(target, "assign cpu_irq = (sys_io_irq || user_io_irq);")
        )
        bad_code, bad_log = prove(negative, directory / f"negative{multiplier}.log")
        results.append(
            {
                "clock_multiplier": multiplier,
                "passed": code == 0 and "SUCCESS" in log,
                "negative_control_detected": bad_code != 0 and "proof did fail" in bad_log,
                "serial_connections": connections,
            }
        )
    stable = before == build.hashes([ROOT / name for name in before]) and build.generation_current()
    report = {
        "passed": stable and all(r["passed"] and r["negative_control_detected"] for r in results),
        "inputs": before,
        "inputs_unchanged": stable,
        "clocks": results,
        "artifacts": build.hashes(list(directory.glob("*.v")) + list(directory.glob("*.log"))),
        "scope": "Actual generated parent cones and byte-identical standalone decoder/serial children at both clocks. Arbitrary CPU address, accepted-enable, direction, child read bytes and interrupt pins: mirrored strobes, peripheral mux, slow classification, ROM write gate, command exclusion and serial IRQ reach the CPU. Child bodies are removed only to expose independent child output inputs; decoder body remains actual RTL.",
        "limits": "CPU enable/tick histories are cut inputs here and require the separate actual clock proof. Does not prove CPU interrupt polling, registered read latency, warm reset, receive traffic or universal whole-game equivalence.",
    }
    write_json(directory / "result.json", report)
    print(f"Actual parent bus/serial binding: {report['passed']}", flush=True)
    if not report["passed"]:
        raise ValueError("Actual parent bus binding failed; inspect retained proofs")
    return report


if __name__ == "__main__":
    qualify()
