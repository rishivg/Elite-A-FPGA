"""Prove actual machine VIA/ADC clock wiring against the declared PHI2 profile."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from tools.validation.common import ROOT, write_json

from . import build, video96


def observed_parent(text, name):
    """Add observation wires only, including each real child port expression."""
    match = re.search(r"^module\s+" + name + r"\b[\s\S]*?^endmodule\b", text, re.M)
    if match is None:
        raise ValueError("Missing actual machine module")
    parent = match[0]
    clocks = {}
    for module, instance, ports in (
        ("Via6522", "sys", ("io_tick", "io_phi2")),
        ("Via6522", "user", ("io_tick", "io_phi2")),
        ("Adc7002", "adc", ("io_tick",)),
    ):
        child = re.search(r"\b" + module + r"\s+" + instance + r"\s*\(([\s\S]*?)\n\s*\);", parent)
        if child is None:
            raise ValueError("Missing actual peripheral instance: " + instance)
        for port in ports:
            signal = re.search(r"\." + port + r"\s*\(([^()]*)\)", child[1])
            if signal is None:
                raise ValueError("Missing peripheral clock port: " + instance + "." + port)
            clocks[instance + "_" + port] = signal[1].strip()
    clocks.update(clock32="clock32", tickCpu="tickCpu")
    additions = "\n".join(
        f"  wire proof_{port};\n  assign proof_{port} = {signal};"
        for port, signal in clocks.items()
    )
    return text[: match.end() - len("endmodule")] + additions + "\n" + text[
        match.end() - len("endmodule") :
    ], clocks


def expected_profile(multiplier, name):
    return f"""module {name}(
input clk, reset,
output proof_sys_io_tick, proof_sys_io_phi2, proof_user_io_tick,
output proof_user_io_phi2, proof_adc_io_tick, proof_clock32, proof_tickCpu);
reg [4:0] phase_1;
reg [1:0] _zz_clock32;
always @(posedge clk or posedge reset)
 if (reset) begin phase_1 <= 0; _zz_clock32 <= 0; end
 else begin
  _zz_clock32 <= _zz_clock32 == 2 ? 0 : _zz_clock32 + 1'b1;
  if (proof_clock32) phase_1 <= phase_1 + 1'b1;
 end
assign proof_clock32 = {"1'b1" if multiplier == 1 else "(_zz_clock32 == 2)"};
assign proof_tickCpu = proof_clock32 && phase_1[3:0] == 15;
assign proof_sys_io_tick = proof_clock32 && phase_1 == 15;
assign proof_user_io_tick = proof_sys_io_tick;
assign proof_adc_io_tick = proof_sys_io_tick;
assign proof_sys_io_phi2 = !phase_1[4];
assign proof_user_io_phi2 = proof_sys_io_phi2;
endmodule
"""


def qualify(multiplier=1, *, source=None, directory=None):
    if multiplier not in {1, 3}:
        raise ValueError("Unsupported machine clock")
    supplied = source is not None
    if not supplied:
        if not build.generation_current():
            raise ValueError("Generate the current actual machine first")
        if multiplier == 3:
            video96.generate()
        source = (video96.DIRECTORY if multiplier == 3 else build.BBC) / (
            "BbcMachine96.v" if multiplier == 3 else "BbcMachine.v"
        )
    top = "BbcMachine96" if multiplier == 3 else "BbcMachine"
    directory = directory or build.BBC / f"machine-clock-proof{multiplier}"
    directory.mkdir(exist_ok=True)
    dependencies = [source, ROOT / "rtl/bbc/BbcCpu.v", Path(__file__)]
    before = build.hashes(dependencies)
    original = source.read_text()
    observed, connections = observed_parent(original, top)
    actual = directory / "observed.v"
    actual.write_text(observed)
    reference = directory / "expected.v"
    reference.write_text(expected_profile(multiplier, "ClockExpected"))
    children = (
        "BbcCpu BbcCpuEvents BbcRam Via6522 Adc7002 BbcVideo BbcAudio Intel8271 BbcSystemPins"
    )
    command = (
        f"read_verilog {actual} {ROOT / 'rtl/bbc/BbcCpu.v'}; blackbox {children}; "
        f"hierarchy -top {top}; proc; delete -output {top}/o:*; "
        f"delete -input {top}/i:io_*; expose {top}/w:proof_*; opt_clean -purge; "
        f"rename {top} ClockActual; write_rtlil {directory / 'actual.il'}; "
        f"read_verilog {reference}; proc; opt_clean; "
        "equiv_make ClockExpected ClockActual ClockEquivalence; hierarchy -top ClockEquivalence; "
        "async2sync; equiv_simple; equiv_induct -seq 4; equiv_status -assert"
    )
    with (directory / "proof.log").open("w") as log:
        result = subprocess.run(
            ["yosys", "-Q", "-T", "-p", command],
            cwd=source.parent,
            stdout=log,
            stderr=subprocess.STDOUT,
            timeout=120,
        )
    # Change the real ADC port connection, not the observation alias. The
    # proof must reject an ADC accidentally clocked at both CPU endpoints.
    child = re.search(r"\bAdc7002\s+adc\s*\(([\s\S]*?)\n\s*\);", original)
    if child is None:
        raise ValueError("Missing ADC negative-control target")
    wrong_child, replacements = re.subn(
        r"(\.io_tick\s*\()([^()]*)(\))", r"\g<1>tickCpu\g<3>", child[0], count=1
    )
    if replacements != 1:
        raise ValueError("Missing ADC tick connection")
    negative_text = original[: child.start()] + wrong_child + original[child.end() :]
    negative = directory / "wrong-adc-clock.v"
    negative.write_text(observed_parent(negative_text, top)[0])
    negative_command = command.replace(str(actual), str(negative)).replace(
        str(directory / "actual.il"), str(directory / "negative.il")
    )
    with (directory / "negative.log").open("w") as log:
        rejected = subprocess.run(
            ["yosys", "-Q", "-T", "-p", negative_command],
            cwd=source.parent,
            stdout=log,
            stderr=subprocess.STDOUT,
            timeout=120,
        )
    detected = (
        rejected.returncode != 0
        and re.search(
            r"ERROR: Found [1-9]\d* unproven \$equiv cells in 'equiv_status -assert'",
            (directory / "negative.log").read_text(),
        )
        is not None
    )
    unchanged = before == build.hashes(dependencies) and (supplied or build.generation_current())
    if multiplier == 3 and not supplied:
        unchanged = (
            unchanged
            and json.loads((video96.DIRECTORY / "elaboration.json").read_text())["outputs"]
            == video96.snapshot()["outputs"]
        )
    report = {
        "passed": result.returncode == 0 and unchanged and detected,
        "negative_control_detected": detected,
        "root_generation_bound": not supplied,
        "inputs": before,
        "connections": connections,
        "inputs_unchanged": unchanged,
        "artifacts": build.hashes(
            [
                actual,
                reference,
                directory / "actual.il",
                directory / "proof.log",
                negative,
                directory / "negative.il",
                directory / "negative.log",
            ]
        ),
        "clock_multiplier": multiplier,
        "scope": "Actual generated parent clock and reset logic, with observation wires only. Real system/user VIA PHI2 and tick and ADC tick connections are proved against a 32 MHz divide-by-32 profile (and divide-by-three physical master for 96 MHz). PHI2 is high throughout the first 2 MHz slot and low throughout the second; VIA/ADC tick is the last high-slot edge. No input assumptions or clock-edge omissions.",
        "limits": "Peripheral children are blackboxed to isolate their parent clock cone. This proves the clock and port connections, not serial register-write phase behavior, ADC analogue timing or complete machine/game equivalence.",
    }
    write_json(directory / "result.json", report)
    print(
        f"Actual machine clocks, {multiplier * 32000000} Hz: {report['passed']}; {directory}/result.json",
        flush=True,
    )
    if not report["passed"]:
        raise ValueError("Actual machine clock wiring does not match the declared profile")
    return report


if __name__ == "__main__":
    qualify(1)
    qualify(3)
