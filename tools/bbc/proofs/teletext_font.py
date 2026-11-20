"""Prove integrated synchronous font responses from original source bytes."""

import json
import re
import shutil
import subprocess
from pathlib import Path

from tools.bbc import build, bus_binding_proof
from tools.bbc.proofs.video_cursor import hashes, replace_once
from tools.validation.common import ROOT, source_root, write_json


def qualify():
    directory = build.BBC / "teletext-font-response-contract"
    directory.mkdir(exist_ok=True)
    parent = build.BBC / "full-video/BbcMachine96.v"
    original = source_root("jsbeeb")
    extract = ROOT / "tools/bbc/reference_teletext_font.mjs"
    actual = bus_binding_proof.module(parent.read_text(), "TeletextFont")
    names = re.findall(r'\$readmemb\("([^"]+)"', actual)
    if len(names) != 1:
        raise ValueError("Changed font initialization")
    font = parent.parent / names[0]
    files = [
        Path(__file__),
        Path(bus_binding_proof.__file__),
        Path(__file__).with_name("video_cursor.py"),
        parent,
        font,
        ROOT / "rtl/video/TeletextFont.scala",
        extract,
        *sorted((original / "src").rglob("*.js")),
        Path(shutil.which("yosys")),
        Path(shutil.which("node")),
    ]
    before = hashes(files)
    rows = directory / "original-font.hex"
    subprocess.run(["node", str(extract), str(original), str(rows)], check=True, timeout=30)
    expected = [int(value, 16) for value in rows.read_text().split()]
    emitted = [int(value, 2) for value in font.read_text().split()]
    if len(expected) != 960 or emitted != expected + [0] * 64:
        raise ValueError("Integrated font differs from Original source data")
    observed = replace_once(
        actual, "module TeletextFont (", "module TeletextFont (output wire [13:0] proofState,"
    )
    observed = replace_once(
        observed,
        "endmodule",
        "assign proofState={_zz_1_spinal_port0,_zz_1_spinal_port1,valid_regNext,_zz_io_neighbour_1};\nendmodule",
    )
    reference = directory / "reference.v"
    init = "\n".join(f"rom[{i}]=6'd{value};" for i, value in enumerate(expected + [0] * 64))
    reference.write_text(
        """module FontReference(input clk,reset,input [6:0] character,
input [4:0] raster,output [13:0] state,output [5:0] alpha,neighbour);
reg [5:0] rom[0:1023];
initial begin
"""
        + init
        + """
end
wire valid=character>=32 && raster<20;
wire [9:0] index=(valid?((character-32)*10):0)+(raster>>1);
wire [9:0] adjacent=raster[0]?index+10'd1:index-10'd1;
reg [5:0] a,b;
reg va,vb;
always @(posedge clk)begin a<=rom[index];b<=rom[adjacent];end
always @(posedge clk or posedge reset)begin
 if(reset)begin va<=0;vb<=0;end
 else begin va<=valid;vb<=valid && raster>0 && raster<19;end
end
assign state={a,b,va,vb};
assign alpha=va?a:6'b0;
assign neighbour=vb?b:6'b0;
endmodule
"""
    )
    contract = directory / "contract.v"
    contract.write_text("""module FontContract(input clk,output equivalent);
(* anyseq *) reg reset;
(* anyseq *) reg [6:0] character;
(* anyseq *) reg [4:0] raster;
reg initialReset=1;
always @(posedge clk)begin if(initialReset)assume(reset);initialReset<=0;end
wire [13:0] actual,expected;
wire [5:0] a,b,ea,eb;
TeletextFont dut(.clk(clk),.reset(reset),.io_character(character),.io_raster(raster),
 .proofState(actual),.io_alpha(a),.io_neighbour(b));
FontReference ref(.clk(clk),.reset(reset),.character(character),.raster(raster),
 .state(expected),.alpha(ea),.neighbour(eb));
assign equivalent=actual==expected && a==ea && b==eb;
endmodule
""")
    broken = directory / "fault-font.bin"
    bad = emitted.copy()
    bad[333] ^= 1
    broken.write_text("\n".join(f"{value:06b}" for value in bad) + "\n")
    faults = [
        ("positive", observed),
        (
            "changed-source-font",
            replace_once(observed, '"' + names[0] + '"', '"' + str(broken) + '"'),
        ),
        (
            "lost-alpha-capture",
            replace_once(
                observed,
                "_zz_1_spinal_port0 <= _zz_1[_zz_io_alpha];",
                "_zz_1_spinal_port0 <= _zz_1_spinal_port0;",
            ),
        ),
        (
            "wrong-neighbour-direction",
            replace_once(
                observed,
                "assign _zz_io_neighbour = (io_raster[0] ? _zz__zz_io_neighbour : _zz__zz_io_neighbour_1);",
                "assign _zz_io_neighbour = (io_raster[0] ? _zz__zz_io_neighbour_1 : _zz__zz_io_neighbour);",
            ),
        ),
    ]
    results = []
    for name, source in faults:
        rtl = directory / (name + ".v")
        rtl.write_text(source)
        witness = directory / (name + "-counterexample.json")
        witness.unlink(missing_ok=True)
        mode = "-tempinduct -initsteps 2 -maxsteps 6" if name == "positive" else "-seq 4"
        command = f"read_verilog -formal {rtl} {reference} {contract}; prep -top FontContract -flatten; check -assert; memory_map; opt; async2sync; opt_clean; write_json {directory}/{name}.json; sat -set-assumes -set-init-zero {mode} -prove equivalent 1 -verify -timeout 120 -show equivalent -dump_json {witness}"
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
            raise ValueError("Font response contract failed: " + name)
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
            "initialized_rows_verified": 1024,
            "register_bits": 14,
            "scope": "Every actual integrated synchronous font port/state response for all seven-bit characters and five-bit rasters over arbitrary histories and reset after initial reset. All 960 unchanged Original source rows plus 64 padding rows are checked byte-for-byte. Actual ROM and both clocked readers are retained; independent character/row/adjacent lookup equations and validity tags are compared by induction. Three actual font/capture/address faults require initialized counterexamples.",
            "limits": "Source-font lookup and synchronous response contract. Attribute/CRTC phase, glyph rounding, output consumption deadline and dot serialization remain separate. No universal game equivalence.",
            "universal_game_equivalence": False,
        },
    )
    if not stable:
        raise ValueError("Font response inputs changed")


if __name__ == "__main__":
    qualify()
