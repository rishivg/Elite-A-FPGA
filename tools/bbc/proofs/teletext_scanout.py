"""Prove actual teletext dot serialization for arbitrary captured cells."""

import json
import re
import shutil
import subprocess
from pathlib import Path

from tools.bbc import build, bus_binding_proof
from tools.bbc.proofs.video_cursor import hashes, replace_once
from tools.validation.common import ROOT, write_json


def qualify():
    directory = build.BBC / "teletext-scanout-contract"
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
    source = replace_once(
        module,
        "module BbcVideo (",
        "module BbcVideo (input wire proofLoad,proofCursor,proofShown,input wire [11:0] proofDots,input wire [2:0] proofForeground,proofBackground,input wire [10:0] proofX,input wire [9:0] proofY,output wire [47:0] proofState,",
    )
    instance = re.search(r"  Saa5050Cell saa5050Cell_1 \([\s\S]*?\n  \);", source)
    if instance is None:
        raise ValueError("Changed actual cell output boundary")
    source = replace_once(
        source,
        instance[0],
        "assign saa5050Cell_1_io_dots=proofDots;\nassign saa5050Cell_1_io_foreground=proofForeground;\nassign saa5050Cell_1_io_background=proofBackground;",
    )
    source = replace_once(
        source,
        "assign when_BbcVideo_l215 = (clock32 && (switch_BbcVideo_l182 == 5'h06));",
        "assign when_BbcVideo_l215 = proofLoad;",
    )
    for old, new in [
        ("_zz_io_display <= cellCursor;", "_zz_io_display <= proofCursor;"),
        ("_zz_io_x_1 <= (_zz__zz_io_x_1 + _zz__zz_io_x_1_1);", "_zz_io_x_1 <= proofX;"),
        ("_zz_io_y <= cellLine;", "_zz_io_y <= proofY;"),
    ]:
        source = replace_once(source, old, new)
    displays = [
        value
        for value in re.findall(r"_zz_io_display_1 <= [^;]+;", source)
        if "control[4]" in value
    ]
    if len(displays) != 1:
        raise ValueError("Changed shown-cell boundary")
    source = replace_once(source, displays[0], "_zz_io_display_1 <= proofShown;")
    source = replace_once(
        source,
        "endmodule",
        "assign proofState={_zz_when_BbcVideo_l214,_zz_io_x,_zz_io_rgb,_zz_io_rgb_1,_zz_io_rgb_2,_zz_io_display,_zz_io_display_1,_zz_io_x_1,_zz_io_y};\nendmodule",
    )
    source = replace_once(full, module, source)
    contract = directory / "contract.v"
    contract.write_text("""module ScanoutContract(input clk,output equivalent);
(* anyseq *) reg reset,load,cursor,shown,ulaWrite,address;
(* anyseq *) reg [7:0] data;
(* anyseq *) reg [11:0] dots;
(* anyseq *) reg [2:0] fg,bg;
(* anyseq *) reg [10:0] x;
(* anyseq *) reg [9:0] y;
reg initialReset=1;
always @(posedge clk)begin if(initialReset)assume(reset);initialReset<=0;end
wire [47:0] actual;
wire [7:0] control;
wire pixel,display;
wire [2:0] rgb;
wire [10:0] actualX;
wire [9:0] actualY;
BbcVideo dut(.clk(clk),.reset(reset),.proofLoad(load),.proofCursor(cursor),.proofShown(shown),
 .proofDots(dots),.proofForeground(fg),.proofBackground(bg),.proofX(x),.proofY(y),
 .proofState(actual),.io_control(control),.io_pixel(pixel),.io_display(display),
 .io_rgb(rgb),.io_x(actualX),.io_y(actualY),.io_ulaWrite(ulaWrite),.io_address(address),.io_data(data),
 .io_crtcWrite(1'b0),.io_lightPenPulse(1'b0),.io_memoryData(8'b0),.io_scroll(2'b0));
reg [2:0] phase=0;
reg [3:0] dot=0;
reg [11:0] heldDots=0;
reg [2:0] heldFg=0,heldBg=0;
reg heldCursor=0,heldShown=0;
reg [10:0] heldX=0;
reg [9:0] heldY=0;
always @(posedge clk or posedge reset)begin
 if(reset)begin phase<=0;dot<=0;heldDots<=0;heldFg<=0;heldBg<=0;
  heldCursor<=0;heldShown<=0;heldX<=0;heldY<=0;end
 else begin
  phase<=phase+3'd1;
  if(phase==7)dot<=dot==11?0:dot+4'd1;
  if(load)begin dot<=0;heldDots<=dots;heldFg<=fg;heldBg<=bg;
   heldCursor<=cursor;heldShown<=shown;heldX<=x;heldY<=y;end
 end
end
wire [2:0] colour=heldShown?(heldDots[dot]?heldFg:heldBg):3'b0;
assign equivalent=actual=={phase,dot,heldDots,heldFg,heldBg,heldCursor,heldShown,heldX,heldY} &&
 dot<12 && (!control[1] || (pixel==(phase==7 && !load) && display==(heldShown||heldCursor) &&
 rgb==(colour^(heldCursor?3'b111:3'b0)) && actualX==((heldX+dot)&11'h7ff) && actualY==heldY));
endmodule
""")
    faults = [
        ("positive", source),
        (
            "lost-dot-tick",
            replace_once(
                source,
                "assign when_BbcVideo_l214 = (_zz_when_BbcVideo_l214 == 3'b111);",
                "assign when_BbcVideo_l214 = 1'b0;",
            ),
        ),
        (
            "lost-cell-dots",
            replace_once(source, "_zz_io_rgb <= saa5050Cell_1_io_dots;", "_zz_io_rgb <= 12'b0;"),
        ),
        (
            "wrong-cursor-inversion",
            replace_once(
                source,
                "io_rgb = (_zz_io_display ? (_zz_io_rgb_3 ^ 3'b111) : _zz_io_rgb_3);",
                "io_rgb = (_zz_io_display ? (_zz_io_rgb_3 ^ 3'b011) : _zz_io_rgb_3);",
            ),
        ),
    ]
    results = []
    for name, text in faults:
        rtl = directory / (name + ".v")
        rtl.write_text(text)
        witness = directory / (name + "-counterexample.json")
        witness.unlink(missing_ok=True)
        mode = "-tempinduct -initsteps 3 -maxsteps 12" if name == "positive" else "-seq 12"
        command = f"read_verilog -formal {rtl} {contract}; prep -top ScanoutContract -flatten; check -assert; opt; memory_map; opt; async2sync; opt_clean; write_json {directory}/{name}.json; sat -set-assumes -set-init-zero {mode} -prove equivalent 1 -verify -timeout 120 -show equivalent -dump_json {witness}"
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
                s["name"] == "equivalent" and "0" in s.get("wave", "")
                for s in json.loads(witness.read_text())["signal"]
            )
        )
        results.append({"name": name, "accepted": passed})
        if not passed:
            raise ValueError("Teletext scanout failed: " + name)
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
            "register_bits": 48,
            "proofs": results,
            "scope": "All actual teletext serialization state over arbitrary cell-load/dot/colour/cursor/shown/coordinate histories, arbitrary ULA mode writes and reset after initial reset. Actual dot divider, dot counter, held cells, output drivers and update priority are retained; only combinational component/load-data boundaries are abstracted. Every TT pixel/display/RGB/coordinate output is compared while selected, including a simultaneous load/tick. Three actual RTL mutations require initialized counterexamples.",
            "limits": "Free glyph/load/placement boundaries. CRTC, IC15, pin timing, source glyph meaning, reference absolute phase and global integration remain separate. No universal game equivalence.",
            "universal_game_equivalence": False,
        },
    )
    if not stable:
        raise ValueError("Scanout inputs changed")


if __name__ == "__main__":
    qualify()
