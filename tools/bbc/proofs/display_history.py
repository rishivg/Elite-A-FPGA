"""Prove the generated display latch against original horizontal update statements."""

import json
import re
import shutil
import subprocess
from pathlib import Path

from tools.bbc import bus_binding_proof
from tools.bbc.proofs.cpu_reference import prepared
from tools.bbc.proofs.video_cursor import hashes, replace_once
from tools.validation.common import ROOT, source_root, write_json

DIRECTORY = ROOT / "build/bbc/display-source-history"


def source_expressions(text):
    def unique(pattern):
        values = re.findall(pattern, text)
        if len(values) != 1:
            raise ValueError("Changed original horizontal display statement")
        return values[0]

    position = unique(r"const displayEnablePos = ([^;]+);")
    end = unique(
        r"if\s*\(([^{};]+)\)\s*\{\s*this\.dispEnableClear\(HDISPENABLE \| SKEWDISPENABLE\);"
    )
    for fragment in (
        "if (this.horizCounter === displayEnablePos) {\n                this.dispEnableSet(SKEWDISPENABLE);\n            }",
        "this.endOfScanline();\n                this.horizCounter = 0;\n                this.dispEnableSet(HDISPENABLE);",
        "if (skew < 3) {\n                        this.video.displayEnableSkew = skew;\n                        this.video.dispEnableSet(USERDISPENABLE);",
    ):
        if text.count(fragment) != 1:
            raise ValueError("Changed original display update order or skew retention")
    order = [
        text.index(fragment)
        for fragment in (
            "const displayEnablePos",
            "this.dispEnableSet(SKEWDISPENABLE)",
            "this.dispEnableClear(HDISPENABLE | SKEWDISPENABLE)",
            "this.dispEnableSet(HDISPENABLE)",
        )
    ]
    if order != sorted(order):
        raise ValueError("Changed original set/clear ordering")
    replacements = {
        "this.displayEnableSkew": "retainedWide",
        "this.teletextMode": "teletext",
        "this.horizCounter": "horizontalWide",
        "this.regs[1]": "widthWide",
        "this.regs[0]": "totalWide",
        "displayEnablePos": "position",
        "===": "==",
    }
    converted = []
    for expression in (position, end):
        for old, new in replacements.items():
            expression = expression.replace(old, new)
        expression = " ".join(expression.split())
        if expression not in (
            "retainedWide + (teletext ? 2 : 0)",
            "horizontalWide == widthWide + position || horizontalWide == totalWide + position",
        ):
            raise ValueError("Unsupported original display expression")
        converted.append(expression)
    return converted


def reference(position, end):
    return f"""module DisplaySourceReference(input clk,reset,step,write,ulaWrite,address,vertical,raster3,
 input [7:0] data,horizontal,output [28:0] state);
reg [4:0] index=0;
reg [7:0] width=0,total=0;
reg [1:0] retained=0,skew=0;
reg teletext=0,displayed=0,skewed=0,cell=0;
wire [31:0] retainedWide={{30'd0,retained}},horizontalWide={{24'd0,horizontal}};
wire [31:0] widthWide={{24'd0,width}},totalWide={{24'd0,total}};
// JavaScript number arithmetic is exact here: all sums lie between 0 and 259.
wire [31:0] position={position};
wire ending={end};
wire nextSkew=(skewed || horizontalWide==position) && !ending;
wire nextDisplay=displayed && !ending;
always @(posedge clk or posedge reset)
 if(reset)begin index<=0;width<=0;total<=0;retained<=0;skew<=0;teletext<=0;displayed<=0;skewed<=0;cell<=0;end
 else begin
  // Original polltime runs before the CPU's subsequent register write.
  if(step)begin
   cell<=nextDisplay && nextSkew && vertical && !raster3 && skew!=3 && !teletext;
   displayed<=horizontal==total ? 1'b1:nextDisplay;
   skewed<=nextSkew;
  end
  if(write && !address)index<=data[4:0];
  if(write && address)begin
   if(index==0)total<=data;
   if(index==1)width<=data;
   if(index==8)begin skew<=data[5:4];if(data[5:4]<3)retained<=data[5:4];end
  end
  if(ulaWrite && !address)teletext<=data[1];
 end
assign state={{index,width,total,skew,teletext,retained,displayed,skewed,cell}};
endmodule
"""


def qualify():
    DIRECTORY.mkdir(exist_ok=True)
    original = source_root("jsbeeb")
    video_source = original / "src/video.js"
    position, end = source_expressions(video_source.read_text())
    native = ROOT / "tools/bbc/reference_display_history.mjs"
    parents = [
        ROOT
        / "build/bbc/equivalence-current"
        / f"generated{m}"
        / ("BbcMachine.v" if m == 1 else "BbcMachine96.v")
        for m in (1, 3)
    ]
    inputs = [
        Path(__file__),
        Path(bus_binding_proof.__file__),
        Path(prepared.__file__),
        Path(__file__).with_name("video_cursor.py"),
        ROOT / "tools/bbc/proofs/memory_composition.py",
        ROOT / "rtl/bbc/BbcVideo.scala",
        native,
        *sorted((original / "src").rglob("*.js")),
        *parents,
        Path(shutil.which("node")),
        Path(shutil.which("yosys")),
    ]
    before = hashes(inputs)
    corpus = DIRECTORY / "original-transitions.json"
    subprocess.run(["node", str(native), str(original), str(corpus)], check=True, timeout=60)
    if json.loads(corpus.read_text()) != {"passed": True, "observations": 196608}:
        raise ValueError("Incomplete original display transition check")
    golden = DIRECTORY / "reference.v"
    golden.write_text(reference(position, end))
    checks = []
    for multiplier, parent in zip((1, 3), parents):
        body = bus_binding_proof.module(parent.read_text(), "BbcVideo")
        body = replace_once(
            body,
            "module BbcVideo (",
            "module BbcVideo (output wire [28:0] proofState,output wire [7:0] proofHorizontal,output wire proofStep,proofVertical,proofRaster3,",
        )
        body = replace_once(
            body,
            "endmodule",
            "assign proofState={index,writableRegs_1,writableRegs_0,skew,control[1],retainedSkew,horizontalShown,skewShown,cellDisplay};\nassign proofHorizontal=crtc_io_horizontal;\nassign proofStep=io_memoryRead;\nassign proofVertical=crtc_io_displayActive;\nassign proofRaster3=crtc_io_raster[3];\nendmodule",
        )
        contract = DIRECTORY / "contract.v"
        contract.write_text("""module DisplayHistory(input clk,output equivalent);
(* anyseq *) reg reset,write,ulaWrite,address;
(* anyseq *) reg [7:0] data;
wire step,vertical,raster3;wire [7:0] horizontal;wire [28:0] actual,expected;
BbcVideo dut(.clk(clk),.reset(reset),.io_crtcWrite(write),.io_ulaWrite(ulaWrite),.io_address(address),.io_data(data),.io_lightPenPulse(1'b0),.io_scroll(2'd0),.io_memoryData(8'd0),.proofState(actual),.proofHorizontal(horizontal),.proofStep(step),.proofVertical(vertical),.proofRaster3(raster3));
DisplaySourceReference ref(.clk(clk),.reset(reset),.step(step),.write(write),.ulaWrite(ulaWrite),.address(address),.data(data),.horizontal(horizontal),.vertical(vertical),.raster3(raster3),.state(expected));
assign equivalent=actual==expected;
endmodule
""")
        variants = [
            ("positive", body),
            (
                "lost-blanking-history",
                replace_once(
                    body,
                    "assign shownNow = (horizontalShown && (! endDisplay));",
                    "assign shownNow = (! endDisplay);",
                ),
            ),
            (
                "lost-skew-retention",
                replace_once(body, "retainedSkew <= io_data[5 : 4];", "retainedSkew <= 2'b00;"),
            ),
        ]
        for name, source in variants:
            case = DIRECTORY / f"{multiplier}-{name}"
            case.mkdir(exist_ok=True)
            rtl, graph, log, witness = [
                case / p for p in ("actual.v", "graph.json", "proof.log", "witness.json")
            ]
            rtl.write_text(source)
            children = " ".join(
                "BbcVideo/c:" + name for name in re.findall(r"^\s+\w+\s+(\w+)\s*\(\s*$", body, re.M)
            )
            command = f"read_verilog -formal {rtl} {golden} {contract}; delete {children}; cutpoint BbcVideo/w:io_memoryRead BbcVideo/w:crtc_io_horizontal BbcVideo/w:crtc_io_displayActive BbcVideo/w:crtc_io_raster; prep -top DisplayHistory -flatten; check -assert; async2sync; opt; memory_map; opt; write_json {graph}"
            prepared.prepare(case, command, [rtl, golden, contract], [graph])
            if any(
                c["type"] in ("$assume", "$check")
                for c in json.loads(graph.read_text())["modules"]["DisplayHistory"][
                    "cells"
                ].values()
            ):
                raise ValueError("Display reference history acquired an assumption")
            witness.unlink(missing_ok=True)
            positive = name == "positive"
            mode = "-tempinduct -initsteps 3 -maxsteps 10" if positive else "-seq 10"
            with log.open("w") as out:
                trial = subprocess.run(
                    [
                        "yosys",
                        "-Q",
                        "-T",
                        "-p",
                        f"read_json {graph}; sat -set-init-zero {mode} -prove equivalent 1 -verify -timeout 30 -show equivalent -dump_json {witness}",
                    ],
                    stdout=out,
                    stderr=subprocess.STDOUT,
                    timeout=120,
                )
            content = log.read_text()
            passed = (
                (trial.returncode == 0 and "Induction step proven: SUCCESS!" in content)
                if positive
                else (
                    trial.returncode != 0
                    and "model found: FAIL!" in content
                    and witness.is_file()
                    and any(
                        s["name"] == "equivalent" and "0" in s.get("wave", "")
                        for s in json.loads(witness.read_text())["signal"]
                    )
                )
            )
            checks.append({"clock_multiplier": multiplier, "name": name, "accepted": passed})
            print(f"{multiplier}-{name}: " + ("PASS" if passed else "FAIL"), flush=True)
            if not passed:
                raise ValueError("Display reference proof incomplete")
    stable = before == hashes(inputs)
    write_json(
        DIRECTORY / "result.json",
        {
            "passed": stable,
            "inputs_unchanged": stable,
            "inputs": before,
            "artifacts": hashes(
                [p for p in DIRECTORY.rglob("*") if p.is_file() and p != DIRECTORY / "result.json"]
            ),
            "checks": checks,
            "source_expressions": {"position": position, "end_display": end},
            "original_transitions": 196608,
            "scope": "Five actual display-history bits and all 24 driving register/index/mode bits at both master rates, against mechanically extracted unmodified original position/end predicates and checked source update order. Arbitrary horizontal/vertical/raster character inputs, reset and live CRTC/ULA writes; includes preserved skew through skew-three writes, non-wrapping thresholds and blanking retention. Original polltime separately executes 196,608 reference-only fixtures. Two actual RTL faults per rate must fail.",
            "limits": "Local horizontal display projection. Character accepts and CRTC coordinates are free proof boundaries. This does not establish original CRTC/global phase, vertical/teletext rendering histories, scheduler translation or whole-game equivalence.",
            "universal_game_equivalence": False,
        },
    )
    if not stable:
        raise ValueError("Display reference inputs changed")


if __name__ == "__main__":
    qualify()
