"""Prove the actual cursor segment pipeline for arbitrary boundary inputs."""

import argparse
import json
import shutil
import subprocess
from pathlib import Path

from tools.bbc import build, bus_binding_proof, video96
from tools.validation.common import ROOT, digest, source_root, write_json


def replace_once(text, old, new):
    if text.count(old) != 1:
        raise ValueError(f"Changed cursor boundary anchor: {old}")
    return text.replace(old, new, 1)


def hashes(files):
    return {
        str(path.relative_to(ROOT) if path.is_relative_to(ROOT) else path): digest(path)
        for path in files
    }


def qualify(clock_multiplier=3):
    top = "BbcVideo"
    directory = build.BBC / (
        "video-cursor-segment-contract"
        if clock_multiplier == 3
        else "video32-cursor-segment-contract"
    )
    directory.mkdir(exist_ok=True)
    actual = (
        video96.DIRECTORY / "BbcMachine96.v"
        if clock_multiplier == 3
        else build.BBC / "BbcMachine.v"
    )
    generation = video96.DIRECTORY / "elaboration.json"
    state = video96.snapshot()
    emitted = json.loads(generation.read_text())
    if emitted["inputs"] != state["inputs"] or emitted["outputs"] != state["outputs"]:
        raise ValueError("Current video elaboration required")
    if clock_multiplier == 1 and not build.generation_current():
        raise ValueError("Current 32 MHz elaboration required")
    original = source_root("jsbeeb")
    reference = Path(__file__).with_name("video_cursor_reference.mjs")
    files = [
        Path(__file__),
        Path(bus_binding_proof.__file__),
        actual,
        generation,
        ROOT / "rtl/bbc/BbcVideo.scala",
        reference,
        Path(shutil.which("yosys")),
        Path(shutil.which("node")),
        *sorted((original / "src").rglob("*.js")),
        *video96.DIRECTORY.glob("*.bin"),
        *build.BBC.glob("*.bin"),
    ]
    before = hashes(files)
    result = directory / "original-reference.json"
    subprocess.run(["node", str(reference), str(original), str(result)], check=True, timeout=60)
    if json.loads(result.read_text()) != {"passed": True, "observations": 24576}:
        raise ValueError("Incomplete Original cursor method check")
    original_source = actual.read_text()
    original_module = bus_binding_proof.module(original_source, top)
    source = replace_once(
        original_module,
        f"module {top} (",
        f"module {top} (input wire proofStep,proofStart,proofVisible, input wire [1:0] proofSkew, input wire [7:0] proofControl, input wire [2:0] proofColour,proofTeletextColour, output wire [2:0] proofIndex, output wire proofCell,",
    )
    source = replace_once(
        source,
        "assign io_memoryRead = (clock32 && (control[4] ? (master[3 : 0] == 4'b0000) : (master == 5'h10)));",
        "assign io_memoryRead = proofStep;",
    )
    source = replace_once(
        source,
        "assign cursorStart = ((((crtc_io_address == cursorAddress) && crtc_io_cursorRaster) && crtc_io_displayActive) && (crtc_io_horizontal < writableRegs_1));",
        "assign cursorStart = proofStart;",
    )
    source = replace_once(
        source,
        "assign _zz_selectedCursor_2 = writableRegs_8[7 : 6];",
        "assign _zz_selectedCursor_2 = proofSkew;",
    )
    for bit in (5, 6, 7):
        source = replace_once(
            source, f"cursorSegment = control[{bit}];", f"cursorSegment = proofControl[{bit}];"
        )
    source = replace_once(
        source,
        "cellCursor <= (crtc_io_cursorVisible && cursorSegment);",
        "cellCursor <= (proofVisible && cursorSegment);",
    )
    source = replace_once(
        source,
        "assign blankedRgb = (cellDisplay ? bitmapRgb : 3'b000);",
        "assign blankedRgb = proofColour;",
    )
    if clock_multiplier == 3:
        source = replace_once(
            source,
            "io_rgb = (_zz_io_display ? (_zz_io_rgb_3 ^ 3'b111) : _zz_io_rgb_3);",
            "io_rgb = proofTeletextColour;",
        )
    end = source.index("endmodule")
    source = (
        source[:end]
        + "assign proofIndex=cursorIndex; assign proofCell=cellCursor;\n"
        + source[end:]
    )
    source = replace_once(original_source, original_module, source)
    contract = directory / "contract.v"
    contract.write_text(
        """module CursorContract(input clk, output equivalent);
(* anyseq *) reg reset,step,start,visible;
(* anyseq *) reg [1:0] skew;
(* anyseq *) reg [7:0] control;
(* anyseq *) reg [2:0] colour;
(* anyseq *) reg ulaWrite,ulaAddress;
(* anyseq *) reg [7:0] ulaData;
(* anyseq *) reg [2:0] teletextColour;
wire [2:0] actualIndex;
wire [2:0] actualRgb;
wire [7:0] actualControl;
wire actualCell;
BbcVideo96 dut(.clk(clk),.reset(reset),.proofStep(step),.proofStart(start),
.proofVisible(visible),.proofSkew(skew),.proofControl(control),
.proofColour(colour),.io_rgb(actualRgb),
.proofTeletextColour(teletextColour),.io_control(actualControl),
.proofIndex(actualIndex),.proofCell(actualCell),.io_ulaWrite(ulaWrite),
.io_crtcWrite(1'b0),.io_address(ulaAddress),.io_data(ulaData),.io_memoryData(8'b0),
.io_lightPenPulse(1'b0),.io_scroll(2'b0));
reg [2:0] index=0;
reg cell=0;
wire [2:0] selected=start ? 3-skew : index;
wire [7:0] mask=selected==3 ? 128 : selected==4 ? 64 : selected>=5 ? 32 : 0;
always @(posedge clk or posedge reset)begin
 if(reset)begin index<=0;cell<=0;end
 else if(step)begin
  cell<=visible && ((control & mask)!=0);
  // Original calls handleCursor only for a nonzero draw index.
  if(selected!=0)index<=(selected+1)%7;
  else if(start)index<=0;
 end
end
assign equivalent=actualIndex==index && actualCell==cell && index<7 &&
 (actualControl[1] || actualRgb==(cell ? colour^3'b111 : colour));
endmodule
""".replace("BbcVideo96 dut", f"{top} dut")
    )
    rows = []
    variants = (
        ("positive", source),
        (
            "disabled-cursor",
            replace_once(
                source, "cellCursor <= (proofVisible && cursorSegment);", "cellCursor <= 1'b0;"
            ),
        ),
        (
            "wrong-segment-width",
            replace_once(
                source, "cursorSegment = proofControl[5];", "cursorSegment = proofControl[6];"
            ),
        ),
        (
            "wrong-inversion",
            replace_once(
                source,
                "(cellCursor ? (blankedRgb ^ 3'b111) : blankedRgb)",
                "(cellCursor ? (blankedRgb ^ 3'b011) : blankedRgb)",
            ),
        ),
        (
            "lost-advance",
            replace_once(
                source,
                "cursorIndex <= ((selectedCursor == 3'b110) ? 3'b000 : _zz_cursorIndex);",
                "cursorIndex <= cursorIndex;",
            ),
        ),
    )
    for name, text in variants:
        rtl = directory / (name + ".v")
        rtl.write_text(text)
        witness = directory / (name + "-counterexample.json")
        proof = "-tempinduct -initsteps 4 -maxsteps 12" if name == "positive" else "-seq 12"
        command = f"read_verilog -formal {rtl} {contract}; prep -top CursorContract -flatten; check -assert; memory_map; opt; async2sync; opt_clean; write_json {directory}/{name}.json; sat -set-init-zero {proof} -prove equivalent 1 -verify -timeout 120 -show equivalent -show actualIndex -show actualCell -dump_json {witness}"
        log = directory / (name + ".log")
        with log.open("w") as output:
            process = subprocess.run(
                ["yosys", "-Q", "-T", "-p", command],
                cwd=actual.parent,
                stdout=output,
                stderr=subprocess.STDOUT,
                timeout=300,
            )
        accepted = (
            process.returncode == 0 and "Induction step proven: SUCCESS!" in log.read_text()
            if name == "positive"
            else process.returncode != 0
            and "ERROR: Called with -verify and proof did fail!" in log.read_text()
            and witness.is_file()
            and any(
                signal["name"] == "equivalent" and "0" in signal.get("wave", "")
                for signal in json.loads(witness.read_text())["signal"]
            )
        )
        rows.append({"name": name, "accepted": accepted})
        if not accepted:
            raise ValueError(f"Cursor segment contract failed: {name}")
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
            "proofs": rows,
            "original_reference_observations": 24576,
            "clock_multiplier": clock_multiplier,
            "scope": "Actual generated cursor index, cell-inversion registers and bitmap RGB output over unbounded clocks, arbitrary reset, advance, cursor start, skew, ULA segment controls, frame visibility and pre-cursor RGB colours. Ordinary ULA writes/data are also arbitrary, including bitmap/teletext mode switches. Combinational boundaries are abstracted; actual cursor state drivers and priority are preserved. Only the teletext RGB branch is abstracted and its colours are excluded from output equality; cursor state is checked across all mode histories and bitmap RGB whenever selected. Public Original cursor methods independently check segment selection, advancement and RGB inversion. Four actual state/output mutations require initialized counterexamples.",
            "limits": "Segment pipeline only. Start detection, CRTC blink/raster behavior, master timing, RGB hold phase, palette/blanking and parent integration remain separate. No universal game equivalence.",
            "universal_game_equivalence": False,
        },
    )
    if not stable:
        raise ValueError("Cursor proof inputs changed")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--clock-multiplier", type=int, choices=(1, 3), default=3)
    qualify(parser.parse_args().clock_multiplier)
