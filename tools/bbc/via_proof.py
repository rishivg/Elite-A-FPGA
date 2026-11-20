"""Prove control-pin preservation with checked T2/serial additions in both layouts."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

from tools.validation.common import ROOT, digest, write_json

from . import baselines, build
from .probe import device_rtl

BASELINE = "563d6a40ace8116d37a9e2e21a73c989ce0241de"
MODULES = ["ViaControlPins", "ViaSerial", "Via6522"]
NEW_OUTPUTS = [
    "ca2Pin",
    "cb2Pin",
    "ca2Output",
    "cb2Output",
    "ca2Rise",
    "cb2Rise",
    "ca2Fall",
    "cb2Fall",
]


def with_split_timer2(baseline, production):
    """Share only the intentional counter fix, independently checked by two models.

    Together with the shared serial addition below, this checks the control-pin
    refactor and remaining VIA behavior. It does not prove the intentional
    additions against the historical implementation that lacked them.
    """

    def replace_once(before, after):
        nonlocal baseline
        if baseline.count(before) != 1:
            raise ValueError("Unrecognized historical T2 block: " + before)
        baseline = baseline.replace(before, after)

    def section(text, begin, end):
        if text.count(begin) != 1 or text.count(end) != 1:
            raise ValueError("Ambiguous T2 preservation section")
        return text[text.index(begin) : text.index(end)]

    replace_once(
        "  val start2 = RegInit(False)",
        "  val start2 = RegInit(False)\n  val reload2 = RegInit(False)",
    )
    replace_once(
        "  val previousPb6 = RegNext(io.portBIn(6)) init (True)\n",
        section(production, "  // PB6 is sampled", "  val timer2Tick = "),
    )
    begin, end = "  val timer2Tick = ", "  when(io.read || io.write)"
    replace_once(section(baseline, begin, end), section(production, begin, end))
    replace_once(
        "        start2 := !acr(5)",
        "        start2 := !acr(5)\n        reload2 := False\n        pulseUnderflow := False",
    )
    replace_once(
        "      is(11) { acr := io.data }",
        "      is(11) {\n        acr := io.data\n"
        "        when(io.data(4 downto 2) =/= B(4, 3 bits) && io.data(3 downto 2) =/= B(1, 2 bits)) { reload2 := False }\n      }",
    )
    replace_once(
        "  val readTimer2 = timer2 + start2.asUInt.resize(16)\n",
        section(production, "  val readTimer2 = ", "  // Counter/PB7 bus readback"),
    )
    begin, end = "  val busTimer2 = ", "  val busInputA = "
    replace_once(section(baseline, begin, end), section(production, begin, end))
    return baseline


def with_external_serial(baseline, production):
    """Share the intentional serial addition, checked by unchanged MOS traces."""

    def replace_once(before, after):
        nonlocal baseline
        if baseline.count(before) != 1:
            raise ValueError("Unrecognized historical serial block: " + before)
        baseline = baseline.replace(before, after)

    def section(begin, end):
        if production.count(begin) != 1 or production.count(end) != 1:
            raise ValueError("Ambiguous serial preservation section")
        return production[production.index(begin) : production.index(end)]

    replace_once(
        "    val tick = in Bool ()", "    val tick = in Bool ()\n    val phi2 = in Bool ()"
    )
    replace_once(
        "    val cb2 = in Bool ()",
        "    val cb2 = in Bool ()\n    val cb1Pin = out Bool ()\n    val cb1Output = out Bool ()",
    )
    replace_once(
        "  val shift = Reg(Bits(8 bits)) init (0)\n",
        section("  val serial = ViaSerial()", "  val serialOutput = "),
    )
    replace_once(
        "  val flags = Reg(Bits(7 bits)) init (0)",
        "  val flags = Reg(Bits(7 bits)) init (0)\n  serial.io.halted := flags(2)",
    )
    replace_once(
        "  when(io.read && io.address === 4)",
        section("  // Serial completion", "  when(io.read && io.address === 4)")
        + "  when(io.read && io.address === 4)",
    )
    replace_once("      is(10) { shift := io.data }\n", "")
    replace_once(
        "    is(10) { io.result := shift }", "    is(10) { io.result := serial.io.result }"
    )
    return baseline


def qualify():
    if not build.generation_current():
        raise ValueError("Run make bbc-gen before proving current VIA RTL")
    directory = build.BBC / "via-proof"
    sources = directory / "sources"
    sources.mkdir(parents=True, exist_ok=True)
    production = device_rtl(MODULES)
    baseline = baselines.historical(BASELINE, "rtl/bbc/Via6522.scala")
    if baseline.count("case class Via6522()") != 1:
        raise ValueError("Unrecognized historical VIA")
    timer2_source = ROOT / "rtl/bbc/Via6522.scala"
    baseline = with_split_timer2(baseline, timer2_source.read_text())
    baseline = with_external_serial(baseline, timer2_source.read_text())
    (sources / "ViaBaseline.scala").write_text(
        baseline.replace("case class Via6522()", "case class ViaBaseline()")
    )
    (sources / "GenerateViaBaseline.scala").write_text(
        "package leet.bbc\nimport spinal.core._\nobject GenerateViaBaseline extends App {\n"
        '  SpinalConfig(targetDirectory = "build/bbc/via-proof/golden",\n'
        "    defaultConfigForClockDomains = ClockDomainConfig(resetActiveLevel = HIGH))\n"
        "    .generateVerilog(ViaBaseline())\n}\n"
    )
    snapshot = build.hashes(
        [*baselines.dependencies(BASELINE), Path(__file__), timer2_source, *sources.glob("*.scala")]
    )
    with (directory / "generate.log").open("w") as log:
        subprocess.run(
            [
                os.environ.get("SBT", "sbt"),
                f'set Compile / unmanagedSourceDirectories += file("{sources}")',
                "runMain leet.bbc.GenerateViaBaseline",
            ],
            cwd=ROOT,
            stdout=log,
            stderr=subprocess.STDOUT,
            check=True,
            timeout=300,
        )
    # Only the previous interface is compared. Demote new output ports to
    # internal wires without removing or modifying their drivers or state.
    gate = re.search(r"^module\s+Via6522\b[\s\S]*?^endmodule\b", production, re.MULTILINE).group()
    for name in NEW_OUTPUTS:
        gate, count = re.subn(
            r"^\s*output wire\s+io_" + name + r",\n", "", gate, flags=re.MULTILINE
        )
        if count != 1:
            raise ValueError("Unrecognized added VIA output: " + name)
    declarations = "\n".join("  wire io_" + name + ";" for name in NEW_OUTPUTS)
    # Preserve the old PCR state name as a read-only relation wire. Its eight
    # bits must also be proved; they are never assumed or driven externally.
    if not re.search(r"wire\s+\[7:0\]\s+controlPins_io_pcr;", gate):
        raise ValueError("Missing integrated PCR state")
    declarations += "\n  (* keep *) wire [7:0] pcr = controlPins_io_pcr;"
    start = gate.index("module Via6522 ")
    endpoint = gate.index(");", start) + 2
    gate = gate[:endpoint] + "\n" + declarations + gate[endpoint:]
    golden = directory / "golden/ViaBaseline.v"
    rtl = directory / "equivalence.v"
    child = re.search(
        r"^module\s+ViaControlPins\b[\s\S]*?^endmodule\b", production, re.MULTILINE
    ).group()
    serial_module = re.search(
        r"^module\s+ViaSerial\b[\s\S]*?^endmodule\b", production, re.MULTILINE
    ).group()
    golden_text, shared = re.subn(
        r"^module\s+ViaSerial\b[\s\S]*?^endmodule\b",
        lambda _: serial_module,
        golden.read_text(),
        flags=re.MULTILINE,
    )
    if shared != 1:
        raise ValueError("Missing shared serial module in preservation baseline")
    rtl.write_text(golden_text + "\n" + gate + "\n" + child)
    command = (
        f"read_verilog -sv {rtl}; proc; opt; flatten ViaBaseline Via6522; "
        "equiv_make ViaBaseline Via6522 via_equiv; hierarchy -top via_equiv; "
        "async2sync; equiv_simple; equiv_induct -seq 2; equiv_status -assert"
    )
    artifacts = build.hashes([golden, rtl])
    with (directory / "proof.log").open("w") as log:
        subprocess.run(
            ["yosys", "-p", command],
            cwd=ROOT,
            stdout=log,
            stderr=subprocess.STDOUT,
            check=True,
            timeout=120,
        )
    if (
        not build.generation_current()
        or production != device_rtl(MODULES)
        or snapshot
        != build.hashes(
            [
                *baselines.dependencies(BASELINE),
                Path(__file__),
                timer2_source,
                *sources.glob("*.scala"),
            ]
        )
        or artifacts != build.hashes([golden, rtl])
    ):
        raise ValueError("VIA proof sources changed during execution")
    output = (directory / "proof.log").read_text()
    count = re.search(r"Of those cells (\d+) are proven and 0 are unproven", output)
    if count is None or "Equivalence successfully proven!" not in output:
        raise ValueError("Incomplete VIA preservation proof")
    receipt = {
        "passed": True,
        "proven_cells": int(count[1]),
        "unproven_cells": 0,
        "baseline_commit": BASELINE,
        "inputs": snapshot,
        "generated": artifacts,
        "generated_model": digest(build.BBC / "BbcMachine.v"),
        "yosys": subprocess.check_output(["yosys", "-V"], text=True).strip(),
        "command": command,
        "scope": "All earlier VIA outputs plus generated CB1 clock/direction, reset and unrestricted inputs; no assumptions. The independently checked T2 and all serial modes are shared by both layouts. Added control outputs are internal wires; separate original-model tests check their declared stable-endpoint behavior.",
        "adapted_behavior": "Split T2 low-byte relatch/high-byte borrow, initial low-byte wrap and the external serial child are present in both layouts. Their correctness comes from independent comparisons, not equivalence to the historical implementation that lacked them.",
        "limits": "Preserves control-pin refactor and remaining port/timer/IRQ behavior with shared intentional additions. Does not independently establish serial correctness, disputed T2 transitions, light-pen integration, coincident output-bank transitions or physical pin timing.",
    }
    write_json(directory / "result.json", receipt)
    print(f"VIA preservation: {count[1]} proven cells; 0 unproven")
    return receipt


if __name__ == "__main__":
    qualify()
