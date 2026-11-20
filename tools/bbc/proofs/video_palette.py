"""Check live ULA palette state and bitmap colour decoding against the reference rules."""

import argparse
import json
import shutil
import subprocess
from pathlib import Path

from tools.bbc import build, bus_binding_proof, video96
from tools.bbc.proofs.video_cursor import hashes, replace_once
from tools.validation.common import ROOT, source_root, write_json


def qualify(clock_multiplier=3):
    directory = build.BBC / (
        "video-palette-contract" if clock_multiplier == 3 else "video32-palette-contract"
    )
    directory.mkdir(exist_ok=True)
    actual = (
        video96.DIRECTORY / "BbcMachine96.v"
        if clock_multiplier == 3
        else build.BBC / "BbcMachine.v"
    )
    if not build.generation_current():
        raise ValueError("Current parent elaboration required")
    original_root = source_root("jsbeeb")
    original = original_root / "src/video.js"
    reference = Path(__file__).with_name("video_palette_reference.mjs")
    generation = video96.DIRECTORY / "elaboration.json"
    state = video96.snapshot()
    emitted = json.loads(generation.read_text())
    if emitted["inputs"] != state["inputs"] or emitted["outputs"] != state["outputs"]:
        raise ValueError("Generated 96 MHz RTL is stale")
    files = [
        Path(__file__),
        Path(bus_binding_proof.__file__),
        Path(__file__).with_name("video_cursor.py"),
        actual,
        ROOT / "rtl/bbc/BbcVideo.scala",
        original,
        reference,
        generation,
        Path(shutil.which("yosys")),
        Path(shutil.which("node")),
        *sorted((original_root / "src").rglob("*.js")),
        *video96.DIRECTORY.glob("*.bin"),
        *build.BBC.glob("*.bin"),
    ]
    before = hashes(files)
    reference_result = directory / "original-reference.json"
    with (directory / "original-reference.log").open("w") as log:
        subprocess.run(
            ["node", str(reference), str(original_root), str(reference_result)],
            stdout=log,
            stderr=subprocess.STDOUT,
            cwd=ROOT,
            check=True,
            timeout=60,
        )
    reference_report = json.loads(reference_result.read_text())
    if reference_report.get("passed") is not True or reference_report.get("observations") != 524288:
        raise ValueError("Incomplete Original palette/decoder reference check")
    full = actual.read_text()
    actual_module = bus_binding_proof.module(full, "BbcVideo")
    source = replace_once(
        actual_module,
        "module BbcVideo (",
        "module BbcVideo (output wire [63:0] proofContents, output wire [3:0] proofPosition, output wire [2:0] proofBitmap,",
    )
    end = source.index("endmodule")
    contents = "\n".join(
        f"assign proofContents[{4 * i}+:4]=paletteValid[{i}]?palette[{i}]:4'b0;" for i in range(16)
    )

    source = (
        source[:end]
        + contents
        + "\nassign proofPosition=physicalPixel;\nassign proofBitmap=bitmapRgb;\n"
        + source[end:]
    )
    source = replace_once(full, actual_module, source)
    observed = directory / "observed.v"
    observed.write_text(source)
    contract = directory / "contract.v"
    contract.write_text("""module PaletteContract(input clk, output equivalent);
(* anyseq *) reg reset,write,address;
(* anyseq *) reg [7:0] data,memoryData;
wire [7:0] actualControl;
wire [63:0] actualPalette,contents;
wire [3:0] position;
wire [2:0] actualBitmap;
BbcVideo dut(.clk(clk),.reset(reset),.io_ulaWrite(write),.io_address(address),.io_data(data),
.io_memoryData(memoryData),.io_crtcWrite(1'b0),.io_lightPenPulse(1'b0),.io_scroll(2'b0),
.io_control(actualControl),.io_palette(actualPalette),.proofContents(contents),.proofPosition(position),.proofBitmap(actualBitmap));
reg [7:0] control=0;
reg [63:0] palette=0;
always @(posedge clk or posedge reset)begin
 if(reset)begin control<=0;palette<=0;end
 else if(write)begin
  if(address)palette[4*data[7:4]+:4]<=data[3:0];
  else control<=data;
 end
end
// Independent translation of the original table4bpp construction: advance
// the byte one place with a low-bit one, then select bits 7,5,3,1.
reg [7:0] shifted;
integer i,offset;
reg [3:0] index,colour;
reg [2:0] expectedBitmap;
always @* begin
 offset=position >> (3-control[3:2]);
 shifted=memoryData;
 for(i=0;i<16;i=i+1)if(i<offset)shifted={shifted[6:0],1'b1};
 index={shifted[7],shifted[5],shifted[3],shifted[1]};
 colour=palette[4*index+:4];
 expectedBitmap=colour[2:0]^3'b111;
 if(colour[3]&&control[0])expectedBitmap=colour[2:0];
end
assign equivalent=actualControl==control&&actualPalette==palette&&contents==palette&&actualBitmap==expectedBitmap;
endmodule
""")
    faults = [
        ("positive", source),
        (
            "dropped-flash-write",
            source.replace(
                "assign paletteWrite = (io_ulaWrite && io_address);",
                "assign paletteWrite = (io_ulaWrite && io_address && !io_data[3]);",
            ),
        ),
        (
            "wrong-flash-colour",
            source.replace(
                "assign bitmapRgb = ((colour[3] && control[0]) ? colour[2 : 0] : (colour[2 : 0] ^ 3'b111));",
                "assign bitmapRgb = (colour[2 : 0] ^ 3'b111);",
            ),
        ),
    ]
    rows = []
    for name, text in faults:
        if name != "positive" and text == source:
            raise ValueError("Missing actual RTL fault anchor: " + name)
        rtl = directory / (name + ".v")
        rtl.write_text(text)
        command = f"read_verilog -formal {rtl} {contract}; prep -top PaletteContract -flatten; check -assert; memory_map; opt; async2sync; opt_clean; write_json {directory}/{name}.json; sat -set-init-zero -tempinduct -initsteps 4 -maxsteps 12 -prove equivalent 1 -verify -timeout 120 -dump_json {directory}/{name}-counterexample.json"
        log = directory / (name + ".log")
        with log.open("w") as out:
            result = subprocess.run(
                ["yosys", "-Q", "-T", "-p", command],
                cwd=actual.parent,
                stdout=out,
                stderr=subprocess.STDOUT,
                timeout=600,
            )
        proof = log.read_text()
        accepted = (
            (result.returncode == 0 and "Induction step proven: SUCCESS!" in proof)
            if name == "positive"
            else (
                result.returncode != 0
                and "model found for base case: FAIL!" in proof
                and (directory / (name + "-counterexample.json")).is_file()
            )
        )
        rows.append({"name": name, "accepted": accepted})
        if not accepted:
            raise ValueError("ULA palette contract failed: " + name)
    stable = before == hashes(files)
    report = {
        "passed": stable,
        "inputs": before,
        "inputs_unchanged": stable,
        "artifacts": build.hashes(
            [p for p in directory.iterdir() if p.name != "result.json" and p.is_file()]
        ),
        "proofs": rows,
        "original_reference_observations": 524288,
        "clock_multiplier": clock_multiplier,
        "tools": {"yosys": subprocess.check_output(["yosys", "-V"], text=True).strip()},
        "scope": "All ULA control/palette writes, memory bytes, reset histories and bitmap colour decoding over unbounded clocks. Actual video child inside the generated whole-machine parent; independent translation of pinned Original palette and table4bpp rules. No input assumptions or DUT state injection.",
        "limits": "Physical pixel position is observed from the actual counter. This proves colour decoding at that position, not its timing, CRTC/cursor/blanking/teletext behavior or parent-memory integration. Default BBC palette; NULA extensions excluded.",
        "universal_game_equivalence": False,
    }
    write_json(directory / "result.json", report)
    if not stable:
        raise ValueError("ULA palette inputs changed")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--clock-multiplier", type=int, choices=(1, 3), default=3)
    qualify(parser.parse_args().clock_multiplier)
