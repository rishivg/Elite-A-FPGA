"""Prove integrated three-stage teletext attribute histories."""

import json
import shutil
import subprocess
from pathlib import Path

from tools.bbc import build, bus_binding_proof
from tools.bbc.proofs.video_cursor import hashes, replace_once
from tools.validation.common import ROOT, source_root, write_json


def qualify():
    directory = build.BBC / "teletext-attribute-history-contract"
    directory.mkdir(exist_ok=True)
    parent = build.BBC / "full-video/BbcMachine96.v"
    reference = Path(__file__).with_name("teletext_contract.v")
    public = Path(__file__).with_name("teletext_control_reference.mjs")
    original = source_root("jsbeeb")
    files = [
        Path(__file__),
        Path(bus_binding_proof.__file__),
        Path(__file__).with_name("video_cursor.py"),
        ROOT / "rtl/video/TeletextAttributes.scala",
        parent,
        reference,
        public,
        *sorted((original / "src").rglob("*.js")),
        Path(shutil.which("yosys")),
        Path(shutil.which("node")),
    ]
    before = hashes(files)
    public_output = directory / "original-control-reference.json"
    subprocess.run(["node", str(public), str(original), str(public_output)], check=True, timeout=90)
    observations = json.loads(public_output.read_text())
    if observations != {"passed": True, "observations": 1048576}:
        raise ValueError("Incomplete Original control-code fixtures")
    fields = "queue_0,queue_1,queue_2,colour,background,separated,doubleHeight,secondHalf,wasDouble,graphics,conceal,flashing,hold,held,heldSet,nextSet,scanline,flashCount,dew,disptmg,ra0,cellCharacter,cellSet,cellForeground,cellBackground,cellDouble,cellConceal,cellFlash"
    module = bus_binding_proof.module(parent.read_text(), "TeletextAttributes")
    observed = replace_once(
        module,
        "module TeletextAttributes (",
        "module TeletextAttributes (output wire [76:0] proofState,output wire [51:0] proofOutputs,",
    )
    observed = replace_once(
        observed,
        "endmodule",
        "assign proofState={"
        + fields
        + "};\nassign proofOutputs={io_character,io_glyphSet,io_glyphRaster,io_foreground,io_background,io_suppressed,io_attributes};\nendmodule",
    )
    contract = directory / "contract.v"
    contract.write_text("""module TeletextContract(input clk,output equivalent);
(* anyseq *) reg reset,enable;
(* anyseq *) reg [2:0] operation;
(* anyseq *) reg [7:0] data;
reg initialReset=1;
always @(posedge clk)begin if(initialReset)assume(reset);initialReset<=0;end
wire [76:0] actualState,expectedState;
wire [51:0] actualOutputs,expectedOutputs;
wire invariant;
TeletextAttributes dut(.clk(clk),.reset(reset),.io_enable(enable),.io_operation(operation),
 .io_data(data),.proofState(actualState),.proofOutputs(actualOutputs));
TeletextHistoryReference ref(.clk(clk),.reset(reset),.enable(enable),.operation(operation),
 .data(data),.state(expectedState),.outputs(expectedOutputs),.invariant(invariant));
assign equivalent=actualState==expectedState && actualOutputs==expectedOutputs && invariant;
endmodule
""")
    faults = [
        ("positive", observed),
        (
            "lost-input-advance",
            replace_once(observed, "queue_0 <= queue_1;", "queue_0 <= queue_0;"),
        ),
        (
            "lost-scanline-advance",
            replace_once(observed, "scanline <= (scanline + 4'b0001);", "scanline <= scanline;"),
        ),
        (
            "wrong-flash-period",
            replace_once(
                observed,
                "flashCount <= (flashCount + 6'h01);",
                "flashCount <= (flashCount + 6'h02);",
            ),
        ),
        (
            "lost-character-latch",
            replace_once(
                observed, "cellCharacter <= renderedCharacter;", "cellCharacter <= 7'd32;"
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
        command = f"read_verilog -formal {rtl} {reference} {contract}; prep -top TeletextContract -flatten; check -assert; async2sync; opt_clean; write_json {directory}/{name}.json; sat -set-assumes -set-init-zero {mode} -prove equivalent 1 -verify -timeout 120 -show equivalent -dump_json {witness}"
        log = directory / (name + ".log")
        with log.open("w") as output:
            process = subprocess.run(
                ["yosys", "-Q", "-T", "-p", command],
                cwd=ROOT,
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
            raise ValueError("Teletext attribute history failed: " + name)
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
            "register_bits": 77,
            "proofs": results,
            "original_control_observations": 1048576,
            "scope": "All 77 state bits and every attribute/glyph-selection output of the actual integrated three-stage teletext attribute child over arbitrary enabled/disabled clocks, operations, byte inputs and reset after initial reset. Independent imperative public-control/pin equations, with proved glyph-set and scanline range invariants. Public unchanged Original advance separately checks all seven-bit characters and held bytes with all graphics/hold/double/separation selections and four flash/conceal fixtures. Four actual RTL faults require initialized counterexamples.",
            "limits": "Declared attribute-history contract. Public reference fixtures are finite; the Original four-byte queue plus external IC15 must be composed separately with this integrated three-stage queue. Font latency, glyph rounding, IC15 input/blanking, LOSE/DEW/RA0 production, dot serialization and global machine/reference composition remain separate. No universal game equivalence.",
            "universal_game_equivalence": False,
        },
    )
    if not stable:
        raise ValueError("Teletext history inputs changed")


if __name__ == "__main__":
    qualify()
