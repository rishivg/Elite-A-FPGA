"""Prove actual teletext font inputs settle before the scanout load."""

import json
import shutil
import subprocess
from pathlib import Path

from tools.bbc import build, bus_binding_proof
from tools.bbc.proofs.video_cursor import hashes, replace_once
from tools.validation.common import ROOT, write_json


def qualify():
    directory = build.BBC / "teletext-font-consumption-deadline"
    directory.mkdir(exist_ok=True)
    parent = build.BBC / "full-video/BbcMachine96.v"
    files = [
        Path(__file__),
        Path(bus_binding_proof.__file__),
        Path(__file__).with_name("video_cursor.py"),
        parent,
        ROOT / "rtl/bbc/BbcVideo.scala",
        ROOT / "rtl/video/Saa5050Cell.scala",
        ROOT / "rtl/video/TeletextAttributes.scala",
        Path(shutil.which("yosys")),
        *parent.parent.glob("*.bin"),
    ]
    before = hashes(files)
    full = parent.read_text()
    cell = bus_binding_proof.module(full, "Saa5050Cell")
    observed_cell = replace_once(
        cell, "module Saa5050Cell (", "module Saa5050Cell (output wire [11:0] proofInputs,"
    )
    observed_cell = replace_once(
        observed_cell,
        "endmodule",
        "assign proofInputs={attributes_io_character,attributes_io_glyphRaster};\nendmodule",
    )
    video = bus_binding_proof.module(full, "BbcVideo")
    observed_video = replace_once(
        video, "module BbcVideo (", "module BbcVideo (output wire proofDeadline,"
    )
    observed_video = replace_once(
        observed_video,
        "Saa5050Cell saa5050Cell_1 (",
        "Saa5050Cell saa5050Cell_1 (.proofInputs(proofInputs),",
    )
    observed_video = replace_once(
        observed_video,
        "endmodule",
        """
wire [11:0] proofInputs;
reg [11:0] sampledInputs=0;
reg afterCharacter=0;
always @(posedge clk)sampledInputs<=proofInputs;
always @(posedge clk or posedge reset)begin
 if(reset)afterCharacter<=0;
 else begin
  if(saa5050Cell_1_io_enable && saa5050Cell_1_io_operation==5)afterCharacter<=1;
  if(when_BbcVideo_l215)afterCharacter<=0;
 end
end
assign proofDeadline=reset || !when_BbcVideo_l215 ||
 (afterCharacter && proofInputs==sampledInputs);
endmodule
""",
    )
    source = replace_once(replace_once(full, cell, observed_cell), video, observed_video)
    contract = directory / "contract.v"
    contract.write_text("""module DeadlineContract(input clk,output equivalent);
(* anyseq *) reg reset,crtcWrite,lightPenPulse,ulaWrite,address;
(* anyseq *) reg [7:0] data,memoryData;
(* anyseq *) reg [1:0] scroll;
reg initialReset=1;
always @(posedge clk)begin if(initialReset)assume(reset);initialReset<=0;end
BbcVideo dut(.clk(clk),.reset(reset),.io_crtcWrite(crtcWrite),.io_lightPenPulse(lightPenPulse),
 .io_ulaWrite(ulaWrite),.io_address(address),.io_data(data),.io_memoryData(memoryData),
 .io_scroll(scroll),.proofDeadline(equivalent));
endmodule
""")
    faults = [
        ("positive", source),
        (
            "premature-dot-load",
            replace_once(
                source,
                "assign when_BbcVideo_l215 = (clock32 && (switch_BbcVideo_l182 == 5'h06));",
                "assign when_BbcVideo_l215 = (clock32 && (switch_BbcVideo_l182 == 5'h05));",
            ),
        ),
    ]
    results = []
    for name, text in faults:
        rtl = directory / (name + ".v")
        rtl.write_text(text)
        witness = directory / (name + "-counterexample.json")
        witness.unlink(missing_ok=True)
        mode = "-tempinduct -initsteps 4 -maxsteps 14" if name == "positive" else "-seq 24"
        command = f"read_verilog -formal {rtl} {contract}; prep -top DeadlineContract -flatten; check -assert; opt; memory_map; opt; async2sync; opt_clean; write_json {directory}/{name}.json; sat -set-assumes -set-init-zero {mode} -prove equivalent 1 -verify -timeout 120 -show equivalent -dump_json {witness}"
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
            raise ValueError("Teletext font deadline failed: " + name)
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
            "clock_multiplier": 3,
            "scope": "Actual integrated video scheduling and attribute state, over arbitrary live CRTC/ULA writes, memory bytes, light-pen/scroll inputs and reset after initial reset. At every scanout load, a preceding character update has occurred and current font inputs equal those sampled by the preceding physical master edge. Actual drivers and consumption guard are retained. A premature actual load fault requires an initialized counterexample.",
            "limits": "Font lookup semantics, source font contents, glyph output, pin meaning, reference absolute phase and dot serialization are separate. No universal game equivalence.",
            "universal_game_equivalence": False,
        },
    )
    if not stable:
        raise ValueError("Teletext deadline inputs changed")


if __name__ == "__main__":
    qualify()
