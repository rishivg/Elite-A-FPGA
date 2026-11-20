"""Check the maintained C engine's real cold reset/register prefix."""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pycparser

from tools.bbc import cycle_reference
from tools.bbc.proofs.cpu_reference import frontend, instructions
from tools.bbc.proofs.video_cursor import hashes, replace_once
from tools.validation.common import ROOT, digest, write_json


def prefix(original, state_value, pins_value):
    actual = re.findall(r"  BbcCpu actual\([\s\S]*?\n  \);", original)
    if len(actual) != 1:
        raise ValueError("Changed real reset/register prefix interface")
    fields = instructions.fields("referenceState")
    core = f"""  reg [255:0] referenceState=256'h{state_value:064x};
  reg [63:0] referencePins=64'h{pins_value:016x};
  reg previousFetch=0;
  wire [255:0] nextState;
  wire [63:0] nextPins;
  wire trap;
  wire [63:0] response=(referencePins & ~64'hff0000) | ({{56'b0,returnedData}}<<16);
  M6502ReferenceNext source(.state_in(referenceState),.pins_in(response),
   .state_out(nextState),.pins_out(nextPins),.trap(trap));
  always @(posedge clk) begin
   if(reset) begin
    referenceState<=256'h{state_value:064x};referencePins<=64'h{pins_value:016x};previousFetch<=0;
   end else if(age<26) begin
    referenceState<=nextState;referencePins<=nextPins;previousFetch<=referencePins[25];
   end
  end
  assign address=referencePins[15:0];assign writing=!referencePins[24];
  assign value=referencePins[23:16];assign instruction=previousFetch;
"""
    core += "\n".join(
        f"  assign {port}={fields[name]};"
        for port, name in (
            ("pc", "PC"),
            ("accumulator", "A"),
            ("indexX", "X"),
            ("indexY", "Y"),
            ("stack", "S"),
            ("flags", "P"),
        )
    )
    source = replace_once(original, actual[0], core)
    source = replace_once(source, "module BbcCpuStreamPrefix(", "module CColdPrefix(")
    source = replace_once(
        source,
        "output [7:0] value, accumulator, indexX, indexY, stack, flags, retiredFlags",
        "output [7:0] value, accumulator, indexX, indexY, stack, flags, retiredFlags,\n  output [255:0] proofState, output [63:0] proofPins, output proofTrap",
    )
    source = replace_once(source, "if (age < 30)", "if (age < 26)")
    source = replace_once(
        source,
        "endmodule",
        "assign proofState=referenceState;assign proofPins=referencePins;assign proofTrap=trap;\nendmodule",
    )
    return source


def qualify():
    directory = ROOT / "build/bbc/cpu-c-cold-entry-bridge"
    directory.mkdir(exist_ok=True)
    work = Path("/tmp/fpgaleet-c-cold-entry")
    work.mkdir(exist_ok=True)
    source = ROOT / "build/bbc/reference-cycle/m6502.h"
    upstream = source.with_name("m6502-original.h")
    native = ROOT / "build/bbc/cpu-c-reference-transpiler/native-comparison-result.json"
    saved = json.loads(native.read_text())
    if not saved.get("passed") or not saved.get("inputs_unchanged"):
        raise ValueError("Current independently compiled C adapter comparison required")
    for group in ("inputs", "artifacts"):
        if any(digest(ROOT / name) != sha for name, sha in saved[group].items()):
            raise ValueError("Changed C adapter comparison premise")
    if source.read_text() != cycle_reference.derive(upstream.read_text()):
        raise ValueError("Changed maintained reference source derivation")
    real_prefix = ROOT / "sim/formal/BbcCpuStreamPrefix.v"
    files = [
        Path(__file__),
        Path(frontend.__file__),
        Path(instructions.__file__),
        Path(cycle_reference.__file__),
        Path(__file__).parents[1] / "video_cursor.py",
        real_prefix,
        source,
        upstream,
        native,
        cycle_reference.SOURCE / "lock.json",
        Path(shutil.which("gcc")),
        Path(shutil.which("yosys")),
        *sorted(Path(pycparser.__file__).parent.rglob("*.py")),
    ]
    before = hashes(files)
    copied = work / "reference.h"
    shutil.copyfile(source, copied)
    oracle = work / "initial.c"
    expression = ",".join("(unsigned long long)cpu." + name for name, _ in frontend.FIELDS)
    oracle.write_text(
        '#define CHIPS_IMPL\n#include "reference.h"\n#include <stdio.h>\nint main(void){m6502_t cpu;m6502_desc_t desc={0};uint64_t pins=m6502_init(&cpu,&desc);printf("'
        + " ".join(["%llu"] * (len(frontend.FIELDS) + 1))
        + '\\n",'
        + expression
        + ",(unsigned long long)pins);return 0;}\n"
    )
    binary = work / "initial"
    subprocess.run(
        ["gcc", "-O1", "-std=c11", str(oracle), "-o", str(binary)], check=True, timeout=60
    )
    observed = subprocess.check_output([str(binary)], text=True)
    (work / "initial.log").write_text(observed)
    values = list(map(int, observed.split()))
    if len(values) != len(frontend.FIELDS) + 1:
        raise ValueError("Incomplete native C initialization observation")
    initial = dict(zip([name for name, _ in frontend.FIELDS], values[:-1], strict=True))
    expected = {name: 0 for name, _ in frontend.FIELDS}
    expected.update(P=2, PINS=0x43000000, bcd_enabled=1)
    if initial != expected or values[-1] != expected["PINS"]:
        raise ValueError("Changed default Model B native C power-on profile")
    packed = 0
    for name, width in frontend.FIELDS:
        packed = (packed << width) | initial[name]
    wrapper = work / "prefix.v"
    wrapper.write_text(prefix(real_prefix.read_text(), packed, values[-1]))
    contract = work / "contract.v"
    contract.write_text("""module CColdEntry(input clk,output equivalent);
(* anyconst *) reg [7:0] a,x,y,s,p;
(* anyconst *) reg [15:0] base;
wire [5:0] age;
wire reset,busMatches,prefixReady,writing,instruction,trap;
wire [15:0] address,pc;
wire [7:0] value,accumulator,indexX,indexY,stack,flags,retiredFlags;
wire [255:0] state;
wire [63:0] pins;
CColdPrefix prefix(.clk(clk),.a(a),.x(x),.y(y),.s(s),.p(p),.opcode(8'hea),.base(base),
 .targetAddress(base+16'd13),.targetRead(8'hea),.targetValue(8'b0),.targetWrite(1'b0),
 .retirementStep(6'd29),.step(age),.reset(reset),.busMatches(busMatches),.prefixReady(prefixReady),
 .writing(writing),.instruction(instruction),.address(address),.pc(pc),.value(value),
 .accumulator(accumulator),.indexX(indexX),.indexY(indexY),.stack(stack),.flags(flags),
 .retiredFlags(retiredFlags),.proofState(state),.proofPins(pins),.proofTrap(trap));
assign equivalent=reset || (!trap && busMatches && (age!=26 ||
 (accumulator==a && indexX==x && indexY==y && stack==s && pc==base+16'd13 &&
 flags==((p&8'hcf)|8'h10) && pins[25:24]==2'b11)));
endmodule
""")
    variants = [
        ("positive", source.read_text()),
        (
            "lost-reset-stack-decrement",
            replace_once(
                source.read_text(),
                "case (0x00<<3)|2: _SAD(0x0100|c->S--,c->PC);",
                "case (0x00<<3)|2: _SAD(0x0100|c->S,c->PC);",
            ),
        ),
        (
            "lost-restored-carry",
            replace_once(
                source.read_text(),
                "case (0x28<<3)|3: c->P=(_GD()|M6502_BF)&~M6502_XF",
                "case (0x28<<3)|3: c->P=(_GD()|M6502_BF)&~(M6502_XF|M6502_CF)",
            ),
        ),
    ]
    results = []
    for name, text in variants:
        case = work / name
        case.mkdir(exist_ok=True)
        reference = case / "reference.h"
        reference.write_text(text)
        expanded = subprocess.check_output(
            ["gcc", "-E", "-P", "-"], input=frontend.extract(reference), text=True
        )
        rtl = case / "source.v"
        rtl.write_text(instructions.specialized(expanded, [0, 0xA2, 0x9A, 0xA9, 0x48, 0xA0, 0x28]))
        log = case / "proof.log"
        witness = case / "counterexample.json"
        witness.unlink(missing_ok=True)
        mode = "-tempinduct -initsteps 36 -maxsteps 40" if name == "positive" else "-seq 38"
        command = f"read_verilog -formal -sv {rtl}; read_verilog -formal {wrapper} {contract}; prep -top CColdEntry -flatten; check -assert; opt; async2sync; opt_clean; write_json {case}/graph.json; sat -set-init-zero {mode} -prove equivalent 1 -verify -timeout 120 -show equivalent -show prefix.age -dump_json {witness}"
        with log.open("w") as output:
            run = subprocess.run(
                ["yosys", "-Q", "-T", "-p", command],
                stdout=output,
                stderr=subprocess.STDOUT,
                timeout=180,
            )
        accepted = (
            run.returncode == 0 and "Induction step proven: SUCCESS!" in log.read_text()
            if name == "positive"
            else run.returncode != 0
            and "ERROR: Called with -verify and proof did fail!" in log.read_text()
            and witness.is_file()
            and any(
                signal["name"] == "equivalent" and "0" in signal.get("wave", "")
                for signal in json.loads(witness.read_text())["signal"]
            )
        )
        results.append({"name": name, "accepted": accepted})
        print(name + (": PASS" if accepted else ": FAIL"), flush=True)
        if not accepted:
            raise ValueError("C cold reset/register prefix failed: " + name)
    stable = before == hashes(files)
    write_json(
        directory / "result.json",
        {
            "passed": stable,
            "inputs_unchanged": stable,
            "inputs": before,
            "artifacts": hashes([path for path in work.rglob("*") if path.is_file()]),
            "default_native_state": initial,
            "default_native_pins": values[-1],
            "proofs": results,
            "scope": "Real maintained C power-on/reset and register initialization instructions, default descriptor independently executed by native C. Existing reset/prefix request bytes and symbolic A/X/Y/S/P/base are unchanged. Source tick executes each reset/LDX/TXS/LDA/PHA/LDY/PLP cycle, proving every bus address/direction/write byte and final architectural state, opcode-fetch PC/RW/SYNC and full C flag representation. Reference execution stops at this proved boundary, age 26. Lost reset-stack decrement and lost restored carry require initialized counterexamples.",
            "limits": "Source translator trust remains. The reference's initial Z differs from the FPGA power-on flag and is overwritten by this prefix; no transient flag equality is claimed. Quiet IRQ/NMI, always-enabled logical cycles and declared registered-RTL/direct-C response alignment. Hidden C entry state, arbitrary opcode streams, interrupts/warm reset, real OS entry and full-machine equivalence require separate composition. No actual FPGA state is injected.",
            "universal_game_equivalence": False,
        },
    )
    if not stable:
        raise ValueError("C cold-entry proof inputs changed")


if __name__ == "__main__":
    qualify()
