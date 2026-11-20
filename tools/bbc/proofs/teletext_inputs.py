"""Prove IC15, teletext operation/pin production and captured placement."""

import json
import shutil
import subprocess
from pathlib import Path

from tools.bbc import build, bus_binding_proof
from tools.bbc.proofs.video_cursor import hashes, replace_once
from tools.validation.common import ROOT, write_json


def qualify():
    directory = build.BBC / "teletext-input-placement-contract"
    directory.mkdir(exist_ok=True)
    parent = build.BBC / "full-video/BbcMachine96.v"
    files = [
        Path(__file__),
        Path(bus_binding_proof.__file__),
        Path(__file__).with_name("video_cursor.py"),
        parent,
        ROOT / "rtl/bbc/BbcVideo.scala",
        Path(shutil.which("yosys")),
        *parent.parent.glob("*.bin"),
    ]
    before = hashes(files)
    full = parent.read_text()
    module = bus_binding_proof.module(full, "BbcVideo")
    observed = replace_once(
        module,
        "endmodule",
        """
wire [4:0] expectedStage=master-5'd16;
wire expectedRead=clock32 && expectedStage==1;
wire expectedLoad=clock32 && expectedStage==6;
wire [7:0] width=writableRegs_1;
wire [7:0] displayEnd=width+skew;
wire [7:0] extendedEnd=displayEnd+8'd2;
wire [7:0] startPlus2={6'b0,skew}+8'd2;
wire expectedInputDisplay=crtc_io_displayActive && crtc_io_horizontal>=skew &&
 crtc_io_horizontal<displayEnd;
wire expectedLose=crtc_io_address[13] && crtc_io_displayActive &&
 crtc_io_horizontal<extendedEnd && skew!=3;
reg [7:0] heldInput=0;
reg heldShown=0;
reg [10:0] heldX=0;
reg [9:0] heldY=0;
wire [10:0] column={3'b0,cellHorizontal}-{9'b0,skew}-11'd2;
always @(posedge clk or posedge reset)begin
 if(reset)begin heldInput<=0;heldShown<=0;heldX<=0;heldY<=0;end
 else begin
  if(expectedRead)heldInput<=crtc_io_address[13]?io_memoryData:8'b0;
  if(expectedLoad)begin
   heldShown<=!control[4] && crtc_io_displayActive && crtc_io_address[13] && skew!=3 &&
    crtc_io_horizontal>=startPlus2 && crtc_io_horizontal<extendedEnd;
   heldX<=column*12;heldY<=cellLine;
  end
 end
end
reg expectedEnable;
reg [2:0] expectedOperation;
reg [7:0] expectedData;
always @*begin
 expectedEnable=0;expectedOperation=0;expectedData=0;
 if(clock32)case(expectedStage)
  1:begin expectedEnable=1;expectedOperation=1;
   expectedData=expectedInputDisplay?heldInput:heldInput|8'h40;end
  3:begin expectedEnable=1;expectedOperation=2;expectedData[0]=crtc_io_vsync;end
  4:begin expectedEnable=1;expectedOperation=4;expectedData[0]=crtc_io_rasterPins[0];end
  5:begin expectedEnable=1;expectedOperation=5;end
  7:begin expectedEnable=1;expectedOperation=3;expectedData[0]=expectedLose;end
 endcase
end
initial assume(reset);
wire proofEquivalent=_zz_io_data==heldInput && _zz_io_display_1==heldShown &&
 _zz_io_x_1==heldX && _zz_io_y==heldY && when_BbcVideo_l215==expectedLoad &&
 saa5050Cell_1_io_enable==expectedEnable && saa5050Cell_1_io_operation==expectedOperation &&
 saa5050Cell_1_io_data==expectedData;
endmodule
""",
    )
    source = replace_once(full, module, observed)
    crtc = bus_binding_proof.ports(module, "Crtc6845", "crtc")
    cuts = sorted(
        {
            value.split("[")[0].strip()
            for name, value in crtc.items()
            if name not in {"io_enable", "io_registers", "clk", "reset"}
        }
    )
    variants = [
        ("positive", source),
        (
            "lost-input-latch",
            replace_once(
                source,
                "_zz_io_data <= (crtc_io_address[13] ? io_memoryData : 8'h0);",
                "_zz_io_data <= 8'h0;",
            ),
        ),
        (
            "wrong-blanking-bit",
            replace_once(source, "(_zz_io_data | 8'h40)", "(_zz_io_data | 8'h20)"),
        ),
        (
            "wrong-dew-pin",
            replace_once(
                source,
                "saa5050Cell_1_io_data[0] = crtc_io_vsync;",
                "saa5050Cell_1_io_data[0] = 1'b0;",
            ),
        ),
    ]
    results = []
    for name, text in variants:
        rtl = directory / (name + ".v")
        rtl.write_text(text)
        witness = directory / (name + "-counterexample.json")
        witness.unlink(missing_ok=True)
        mode = "-tempinduct -initsteps 3 -maxsteps 12" if name == "positive" else "-seq 16"
        command = (
            f"read_verilog -formal {rtl}; delete BbcVideo/c:crtc; hierarchy -top BbcVideo; proc; delete -output BbcVideo/o:io_*; cutpoint "
            + " ".join("BbcVideo/w:" + cut for cut in cuts)
            + f"; expose BbcVideo/w:proofEquivalent; opt -purge; flatten; opt -purge; memory_map; opt; async2sync; opt_clean; check -assert; write_json {directory}/{name}.json; sat -set-assumes -set-init-zero {mode} -prove proofEquivalent 1 -verify -timeout 120 -show proofEquivalent -dump_json {witness}"
        )
        log = directory / (name + ".log")
        with log.open("w") as output:
            process = subprocess.run(
                ["yosys", "-Q", "-T", "-p", command],
                cwd=parent.parent,
                stdout=output,
                stderr=subprocess.STDOUT,
                timeout=240,
            )
        content = log.read_text()
        passed = (
            process.returncode == 0 and "Induction step proven: SUCCESS!" in content
            if name == "positive"
            else process.returncode != 0
            and "ERROR: Called with -verify and proof did fail!" in content
            and witness.is_file()
            and any(
                s["name"] == "proofEquivalent" and "0" in s.get("wave", "")
                for s in json.loads(witness.read_text())["signal"]
            )
        )
        results.append({"name": name, "accepted": passed})
        if not passed:
            raise ValueError("Teletext input/placement failed: " + name)
    stable = before == hashes(files)
    write_json(
        directory / "result.json",
        {
            "passed": stable,
            "inputs": before,
            "inputs_unchanged": stable,
            "artifacts": build.hashes(
                [p for p in directory.iterdir() if p.is_file() and p.name != "result.json"]
            ),
            "proofs": results,
            "scope": "Actual IC15 input latch, teletext operation/enable/data production and held shown/coordinate state over arbitrary CRTC output histories, live register/ULA writes, memory bytes and reset after initial reset. Only child CRTC outputs are free cutpoints; all retained state drivers, stages, priorities and gates are actual production logic. Independent MA13, bit-six blanking, DEW/LOSE/RA0, character stage and eleven-bit placement equations. Three actual RTL faults require initialized counterexamples.",
            "limits": "CRTC output meaning and absolute phase are separate; the bitmap/CRTC contracts supply declared digital histories. Font/glyph interpretation, output serialization, reference pipeline alignment and whole-machine composition remain separate. No universal game equivalence.",
            "universal_game_equivalence": False,
        },
    )
    if not stable:
        raise ValueError("Teletext input/placement inputs changed")


if __name__ == "__main__":
    qualify()
