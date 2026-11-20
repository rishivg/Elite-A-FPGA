"""Prove the actual VIA-to-IC32 scroll cone against original transition semantics."""

import json
import re
import subprocess
from pathlib import Path

from tools.validation.common import ROOT, source_root, write_json
from tools.validation.setup import verify

from . import build, video96
from .probe import device_rtl
from .system_pins_proof import observed_module


def parent_binding(text, top):
    """Fail closed if the actual parent disconnects the proved scroll cone."""
    match = re.search(r"^module\s+" + top + r"\b[\s\S]*?^endmodule\b", text, re.M)
    if match is None:
        raise ValueError("Missing actual parent")
    parent = match[0]

    def ports(module, instance):
        child = re.search(r"\b" + module + r"\s+" + instance + r"\s*\(([\s\S]*?)\n\s*\);", parent)
        if child is None:
            raise ValueError("Missing actual scroll child: " + instance)
        return {
            name: re.sub(r"\s+", "", expression)
            for name, expression in re.findall(r"\.(\w+)\s*\(([^()]*)\)", child[1])
        }

    via, pins, video = (
        ports("Via6522", "sys"),
        ports("BbcSystemPins", "systemPins"),
        ports("BbcVideo", "video"),
    )
    if any(
        not set(required).issubset(declared)
        for declared, required in (
            (via, ("io_busPortB", "io_portBIn", "clk", "reset")),
            (pins, ("io_busPortB", "io_scroll", "clk", "reset")),
            (video, ("io_scroll",)),
        )
    ):
        raise ValueError("Missing actual scroll-cone port")
    required = (
        via["io_busPortB"] == pins["io_busPortB"],
        pins["io_scroll"] == video["io_scroll"],
        via["clk"] == pins["clk"] == "clk",
        via["reset"] == pins["reset"] == "reset",
    )
    if not all(required):
        raise ValueError("Actual parent does not bind the proved VIA/IC32 scroll path")
    # Leave the actual port-input computation intact for a separate SAT proof.
    port_input = via["io_portBIn"]
    observed = parent[: parent.rindex("endmodule")] + (
        "wire [7:0] proof_portInput;\nassign proof_portInput = " + port_input + ";\nendmodule"
    )
    return text[: match.start()] + observed + text[match.end() :], {
        "via_bus_port_b": via["io_busPortB"],
        "video_scroll": video["io_scroll"],
        "via_port_input": port_input,
    }


def miter(devices):
    via = re.search(r"^module\s+Via6522\b[\s\S]*?^endmodule\b", devices, re.M)
    if via is None:
        raise ValueError("Missing actual system VIA module")
    text = via[0]
    end = text.index(");")
    text = (
        text[:end]
        + ",\n output wire [3:0] proofOrb, proofDdr\n"
        + text[end : text.rindex("endmodule")]
        + "assign proofOrb = orb[3:0];\nassign proofDdr = ddrb[3:0];\nendmodule\n"
    )
    devices = devices[: via.start()] + text + devices[via.end() :]
    pins = re.search(r"^module\s+BbcSystemPins\b[\s\S]*?^endmodule\b", devices, re.M)
    if pins is None:
        raise ValueError("Missing actual system-pin module")
    devices = devices[: pins.start()] + observed_module(pins[0]) + devices[pins.end() :]
    return (
        devices
        + """
module ScrollEquivalence(
 input clk, reset, tick, phi2, read, write,
 input [3:0] address, externalUpper,
 input [7:0] data, control,
 output equivalent, actualInvariant, expectedInvariant);
 wire [7:0] busPortB, latch;
 wire [1:0] scroll;
 wire [3:0] previous, actualOrb, actualDdr;
 Via6522 via(.clk(clk), .reset(reset), .io_tick(tick), .io_phi2(phi2),
 .io_read(read), .io_write(write), .io_address(address), .io_data(data),
 .io_portAIn(8'hff), .io_portBIn({externalUpper,4'hf}),
 .io_ca1(control[0]), .io_ca2(control[1]), .io_ca2AfterWrite(control[2]),
 .io_cb1(control[3]), .io_cb1Pulse(control[4]), .io_cb2(control[5]),
 .io_busPortB(busPortB), .proofOrb(actualOrb), .proofDdr(actualDdr));
 BbcSystemPins pins(.clk(clk), .reset(reset), .io_keys(128'd0),
 .io_driveA(8'hff), .io_busDriveA(8'hff), .io_busPortB(busPortB),
 .io_latch(latch), .io_scroll(scroll), .proofPrevious(previous));
 // SysVia.write ORB/DDRB and recalculatePortBPins, restricted to PB0..3.
 // Public original APIs independently check every transition in this model.
 reg [3:0] expectedOrb=0, expectedDdr=0, expectedPrevious=0;
 reg [7:0] expectedLatch=0;
 wire [3:0] newOrb = write && address==0 ? data[3:0] : expectedOrb;
 wire [3:0] newDdr = write && address==2 ? data[3:0] : expectedDdr;
 wire [3:0] port = newOrb | ~newDdr;
 always @(posedge clk or posedge reset)
 if (reset) begin
  expectedOrb<=0; expectedDdr<=0; expectedLatch<=0; expectedPrevious<=0;
 end else begin
  expectedOrb<=newOrb; expectedDdr<=newDdr; expectedPrevious<=port;
  // Repeated recalculations are idempotent. This relation is proved below,
  // rather than assuming that a write or an input update changes the byte.
  expectedLatch[port[2:0]]<=port[3];
 end
 assign equivalent = actualOrb==expectedOrb && actualDdr==expectedDdr &&
  busPortB[3:0]==port && latch==expectedLatch &&
  scroll==expectedLatch[5:4] && previous==expectedPrevious;
 assign actualInvariant = latch[previous[2:0]]==previous[3];
 assign expectedInvariant = expectedLatch[expectedPrevious[2:0]]==expectedPrevious[3];
endmodule
"""
    )


def qualify():
    verify()
    if not build.generation_current():
        raise ValueError("Generate the actual machine before proving scroll")
    video96.generate()
    directory = build.BBC / "scroll-proof"
    directory.mkdir(exist_ok=True)
    generated = [build.BBC / "BbcMachine.v", video96.DIRECTORY / "BbcMachine96.v"]
    helper = ROOT / "tools/bbc/reference_scroll_semantics.mjs"
    dependencies = [
        Path(__file__),
        helper,
        ROOT / "tools/bbc/system_pins_proof.py",
        source_root("jsbeeb") / "src/via.js",
        source_root("jsbeeb") / "src/scheduler.js",
        ROOT / "tools/validation/reference.lock.json",
        *generated,
    ]
    before = build.hashes(dependencies)
    subprocess.run(
        ["node", str(helper), str(source_root("jsbeeb")), str(directory / "original.json")],
        check=True,
        timeout=60,
    )
    semantics = json.loads((directory / "original.json").read_text())
    if semantics != {"passed": True, "latch_cases": 65536, "port_cases": 8192}:
        raise ValueError("Incomplete original transition comparison")
    devices = device_rtl(["ViaControlPins", "ViaSerial", "Via6522", "BbcSystemPins"])
    # The actual 96 MHz machine must use these exact children, not a hand copy.
    for module in ("ViaControlPins", "ViaSerial", "Via6522", "BbcSystemPins"):
        pattern = r"^module\s+" + module + r"\b[\s\S]*?^endmodule\b"
        if (
            re.search(pattern, devices, re.M)[0]
            != re.search(pattern, generated[1].read_text(), re.M)[0]
        ):
            raise ValueError("Clock variants differ in actual scroll child: " + module)
    rtl = directory / "miter.v"
    rtl.write_text(miter(devices))
    negative = directory / "wrong-scroll-bit.v"
    wrong = "assign io_scroll = latch[5 : 4];"
    if rtl.read_text().count(wrong) != 1:
        raise ValueError("Missing actual scroll negative-control target")
    negative.write_text(rtl.read_text().replace(wrong, "assign io_scroll = latch[4 : 3];"))

    def execute(command, label, cwd=ROOT):
        result = subprocess.run(
            ["yosys", "-Q", "-T", "-p", command],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=120,
        )
        (directory / (label + ".log")).write_text(result.stdout + result.stderr)
        return result

    def prove(path, label):
        return execute(
            f"read_verilog {path}; prep -top ScrollEquivalence -flatten; async2sync; opt_clean; "
            "sat -set-init-zero -tempinduct -seq 3 -maxsteps 16 -prove equivalent 1 "
            "-prove actualInvariant 1 -prove expectedInvariant 1 -verify -timeout 90",
            label,
        )

    positive, rejected = prove(rtl, "proof"), prove(negative, "negative")
    detected = rejected.returncode != 0 and "proof did fail" in rejected.stderr
    bindings = []
    for source, top in zip(generated, ("BbcMachine", "BbcMachine96"), strict=True):
        observed, connections = parent_binding(source.read_text(), top)
        parent = directory / (top + "-observed.v")
        parent.write_text(observed)
        # Blackbox unrelated children and retain the actual combinational input
        # cone. The local proof above includes complete VIA and IC32 modules.
        command = (
            f"read_verilog {parent} {ROOT / 'rtl/bbc/BbcCpu.v'}; "
            "blackbox BbcCpu BbcCpuEvents BbcRam Via6522 Adc7002 BbcVideo BbcAudio Intel8271 BbcSystemPins; "
            f"hierarchy -top {top}; proc; delete -output {top}/o:*; expose {top}/w:proof_portInput; "
            f"opt_clean -purge; flatten; sat -prove proof_portInput[3:0] 15 -verify"
        )
        bound = execute(command, top + "-binding", source.parent)
        wrong_input = "assign sys_io_portBIn = (8'hff ^ _zz_io_portBIn);"
        if observed.count(wrong_input) != 1:
            raise ValueError("Missing actual pullup negative-control target")
        bad_parent = directory / (top + "-wrong-pullup.v")
        bad_parent.write_text(observed.replace(wrong_input, wrong_input.replace("8'hff", "8'hfe")))
        bad = execute(
            command.replace(str(parent), str(bad_parent)), top + "-binding-negative", source.parent
        )
        binding_negative = bad.returncode != 0 and "proof did fail" in bad.stderr
        bindings.append(
            {
                "machine": top,
                "passed": bound.returncode == 0 and binding_negative,
                "negative_control_detected": binding_negative,
                "connections": connections,
            }
        )
    verify()
    unchanged = before == build.hashes(dependencies) and build.generation_current()
    passed = (
        positive.returncode == 0 and detected and unchanged and all(b["passed"] for b in bindings)
    )
    report = {
        "passed": passed,
        "inputs": before,
        "inputs_unchanged": unchanged,
        "negative_control_detected": detected,
        "original_transition_checks": semantics,
        "parent_bindings": bindings,
        "artifacts": build.hashes(
            list(directory.glob("*.v"))
            + list(directory.glob("*.log"))
            + [directory / "original.json"]
        ),
        "scope": "Temporal induction of all eight IC32 latch bits and both scroll bits through the exact generated system VIA and IC32, from declared reset, for arbitrary accepted writes, reads, tick/PHI2/control histories and upper port inputs. Selected-latch invariants are proved jointly. Actual parent connections and inactive PB0..3 inputs are checked at both master clocks. Original public APIs check all 65,536 latch transitions and 8,192 low-nibble ORB/DDRB transitions.",
        "limits": "Digital scroll-cone proof against an independently checked transition model. It does not prove the CPU, other VIA outputs, keyboard scan, renderer, complete game or physical pins. Unrelated parent children are blackboxed only for port-input binding.",
    }
    write_json(directory / "result.json", report)
    print(f"Actual VIA/IC32 scroll proof: {passed}", flush=True)
    if not passed:
        raise ValueError("Actual scroll proof failed; see retained logs")
    return report


if __name__ == "__main__":
    qualify()
