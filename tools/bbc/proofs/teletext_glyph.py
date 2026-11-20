"""Prove integrated glyph rounding/mosaic logic for every input combination."""

import json
import shutil
import struct
import subprocess
from pathlib import Path

from tools.bbc import build, bus_binding_proof
from tools.bbc.proofs.video_cursor import hashes, replace_once
from tools.validation.common import ROOT, source_root, write_json


def glyph(character, glyph_set, raster, alpha, neighbour):
    if character < 32 or raster >= 20 or glyph_set >= 3:
        return 0, 0
    mosaic = glyph_set != 0 and character & 32
    six = alpha
    if mosaic:
        row = raster // 2
        left, right = (
            (character >> (0 if row < 3 else 2 if row < 7 else 4)) & 1,
            (character >> (1 if row < 3 else 3 if row < 7 else 6)) & 1,
        )
        six = (7 if left else 0) | (56 if right else 0)
        if glyph_set == 2:
            six &= ~9
            if row in (2, 6, 9):
                six = 0
    current = sum(3 << (2 * x) for x in range(6) if six & (1 << x))
    adjacent = sum(3 << (2 * x) for x in range(6) if neighbour & (1 << x))
    dots = current
    if not mosaic:
        dots |= (current >> 1) & adjacent & ~(adjacent >> 1)
        dots |= (current << 1) & adjacent & ~(adjacent << 1)
        dots &= 4095
    weights = 0
    for group in range(4):
        a, b, c = [(dots >> (3 * group + i)) & 1 for i in range(3)]
        for i, weight in enumerate((3 * a, a + 2 * b, 2 * b + c, 3 * c)):
            weights |= weight << (8 * group + 2 * i)
    return dots, weights


def qualify():
    directory = build.BBC / "teletext-glyph-contract"
    directory.mkdir(exist_ok=True)
    parent = build.BBC / "full-video/BbcMachine96.v"
    original = source_root("jsbeeb")
    public = ROOT / "tools/bbc/reference_teletext_glyphs.mjs"
    files = [
        Path(__file__),
        Path(bus_binding_proof.__file__),
        Path(__file__).with_name("video_cursor.py"),
        parent,
        public,
        ROOT / "rtl/video/TeletextGlyphRow.scala",
        *sorted((original / "src").rglob("*.js")),
        Path(shutil.which("yosys")),
        Path(shutil.which("node")),
    ]
    before = hashes(files)
    corpus = directory / "original-glyphs.bin"
    subprocess.run(["node", str(public), str(original), str(corpus)], check=True, timeout=30)
    content = corpus.read_bytes()
    if (
        content[:8] != b"BBCGLYP\x01"
        or len(content) != 12 + 12288 * 8
        or struct.unpack_from("<I", content, 8)[0] != 12288
    ):
        raise ValueError("Incomplete Original glyph corpus")
    for offset in range(12, len(content), 8):
        inputs, expected = struct.unpack_from("<II", content, offset)
        _, weights = glyph(
            inputs & 127,
            (inputs >> 7) & 3,
            (inputs >> 9) & 31,
            (inputs >> 14) & 63,
            (inputs >> 20) & 63,
        )
        if weights != expected:
            raise ValueError("Independent rounding disagrees with public Original glyphs")
    actual = bus_binding_proof.module(parent.read_text(), "TeletextGlyphRow")
    contract = directory / "contract.v"
    contract.write_text("""module GlyphContract(input clk,output equivalent);
(* anyseq *) reg [6:0] character;
(* anyseq *) reg [1:0] glyphSet;
(* anyseq *) reg [4:0] raster;
(* anyseq *) reg [5:0] alpha,neighbour;
wire [11:0] actualDots;
wire [31:0] actualWeights;
TeletextGlyphRow dut(.io_character(character),.io_glyphSet(glyphSet),.io_raster(raster),
 .io_alpha(alpha),.io_neighbour(neighbour),.io_dots(actualDots),.io_weights(actualWeights));
reg [11:0] expected;
reg [31:0] weights;
reg [5:0] six;
reg [11:0] current,adjacent;
reg left,right;
integer row,x,group;
reg a,b,c;
always @* begin
 row=raster>>1;six=alpha;left=0;right=0;current=0;adjacent=0;
 if(glyphSet!=0 && character[5])begin
  if(row<3)begin left=character[0];right=character[1];end
  else if(row<7)begin left=character[2];right=character[3];end
  else begin left=character[4];right=character[6];end
  six={right,right,right,left,left,left};
  if(glyphSet==2)begin
   six[0]=0;six[3]=0;
   if(row==2 || row==6 || row==9)six=0;
  end
 end
 for(x=0;x<12;x=x+1)begin current[x]=six[x/2];adjacent[x]=neighbour[x/2];end
 expected=current;
 if(!(glyphSet!=0 && character[5]))begin
  for(x=0;x<12;x=x+1)begin
   if(x<11)expected[x]=expected[x] | (current[x+1] && adjacent[x] && !adjacent[x+1]);
   if(x>0)expected[x]=expected[x] | (current[x-1] && adjacent[x] && !adjacent[x-1]);
  end
 end
 if(character<32 || raster>=20 || glyphSet>=3)expected=0;
 weights=0;a=0;b=0;c=0;
 for(group=0;group<4;group=group+1)begin
  a=expected[3*group];b=expected[3*group+1];c=expected[3*group+2];
  weights[8*group+:2]=a?3:0;
  weights[8*group+2+:2]=(a?1:0)+(b?2:0);
  weights[8*group+4+:2]=(b?2:0)+(c?1:0);
  weights[8*group+6+:2]=c?3:0;
 end
end
assign equivalent=actualDots==expected && actualWeights==weights;
endmodule
""")
    variants = [
        ("positive", actual),
        ("lost-separated-gap", replace_once(actual, "six[0] = 1'b0;", "six[0] = 1'b1;")),
        (
            "lost-invalid-blanking",
            replace_once(
                actual, "assign io_dots = (valid ? dots : 12'h0);", "assign io_dots = dots;"
            ),
        ),
    ]
    results = []
    for name, source in variants:
        rtl = directory / (name + ".v")
        rtl.write_text(source)
        witness = directory / (name + "-counterexample.json")
        witness.unlink(missing_ok=True)
        command = f"read_verilog -formal {rtl} {contract}; prep -top GlyphContract -flatten; check -assert; opt; write_json {directory}/{name}.json; sat -seq 1 -prove equivalent 1 -verify -timeout 60 -show equivalent -dump_json {witness}"
        log = directory / (name + ".log")
        with log.open("w") as output:
            process = subprocess.run(
                ["yosys", "-Q", "-T", "-p", command],
                cwd=ROOT,
                stdout=output,
                stderr=subprocess.STDOUT,
                timeout=90,
            )
        text = log.read_text()
        passed = (
            process.returncode == 0 and "SAT proof finished - no model found: SUCCESS!" in text
            if name == "positive"
            else process.returncode != 0
            and "ERROR: Called with -verify and proof did fail!" in text
            and witness.is_file()
            and any(
                s["name"] == "equivalent" and "0" in s.get("wave", "")
                for s in json.loads(witness.read_text())["signal"]
            )
        )
        results.append({"name": name, "accepted": passed})
        if not passed:
            raise ValueError("Glyph contract failed: " + name)
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
            "original_glyph_observations": 12288,
            "scope": "Every combinational input combination of the actual integrated glyph child: all characters, sets, rasters, six-bit alpha/adjacent rows; twelve dots and all diagnostic resampling weights. Independent per-dot adjacency equations, mosaic region selection, separated gaps and validity blanking. Original public glyph tables separately check all 12,288 legal-set/character/raster combinations with the original font. Two actual faults require genuine Boolean counterexamples.",
            "limits": "Combinational glyph contract only. Font/attribute timing, output consumption and dot serialization remain separate. No universal game equivalence.",
            "universal_game_equivalence": False,
        },
    )
    if not stable:
        raise ValueError("Glyph inputs changed")


if __name__ == "__main__":
    qualify()
