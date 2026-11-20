"""Prove actual RAM capture precedes the slow teletext input-latch clock."""

import json
import shutil
import subprocess
from pathlib import Path

from tools.bbc import build, bus_binding_proof
from tools.bbc.proofs import video_memory_deadline
from tools.bbc.proofs.video_cursor import hashes, replace_once
from tools.validation.common import ROOT, write_json


def qualify():
    directory = build.BBC / "teletext-memory-response-deadline"
    directory.mkdir(exist_ok=True)
    parent = build.BBC / "full-video/BbcMachine96.v"
    top = "BbcMachine96"
    files = [
        Path(__file__),
        Path(bus_binding_proof.__file__),
        Path(video_memory_deadline.__file__),
        Path(__file__).with_name("video_cursor.py"),
        parent,
        ROOT / "rtl/bbc/BbcMachine.scala",
        ROOT / "rtl/bbc/BbcVideo.scala",
        ROOT / "rtl/bbc/BbcRam.scala",
        Path(shutil.which("yosys")),
        *parent.parent.glob("*.bin"),
    ]
    before = hashes(files)
    original = parent.read_text()
    video = bus_binding_proof.module(original, "BbcVideo")
    latch = """if(clock32) begin
        case(switch_BbcVideo_l182)
          5'h01 : begin
            _zz_io_data <= (crtc_io_address[13] ? io_memoryData : 8'h0);
          end
          default : begin
          end
        endcase
      end"""
    if video.count(latch) != 1 or "assign switch_BbcVideo_l182 = (master - 5'h10);" not in video:
        raise ValueError("Changed actual teletext input-latch guard")
    variants = [
        ("positive", original),
        (
            "lost-fetch",
            replace_once(
                original,
                "assign io_memoryRead = (clock32 && (control[4] ? (master[3 : 0] == 4'b0000) : (master == 5'h10)));",
                "assign io_memoryRead = 1'b0;",
            ),
        ),
        (
            "lost-ready",
            replace_once(original, "videoReady <= io_videoEnable;", "videoReady <= 1'b0;"),
        ),
    ]
    results = []
    for name, source in variants:
        observed, removed, cuts = video_memory_deadline.observe(source, top)
        body = bus_binding_proof.module(observed, top)
        addition = """
wire proofTeletextUse=reset || video_io_control[4] ||
 !(proofClock && proofMaster==17) || (requested && (captured || proofReady));
wire proofTeletextDeadline=proofVideoDeadline && proofTeletextUse;
"""
        observed = replace_once(
            observed, body, body.removesuffix("endmodule") + addition + "endmodule"
        )
        rtl = directory / (name + ".v")
        rtl.write_text(observed)
        witness = directory / (name + "-counterexample.json")
        witness.unlink(missing_ok=True)
        mode = "-tempinduct -initsteps 4 -maxsteps 12" if name == "positive" else "-seq 12"
        target = "proofTeletextDeadline" if name == "positive" else "proofTeletextUse"
        command = (
            f"read_verilog -formal -D SYNTHESIS {rtl}; delete "
            + " ".join(f"{top}/c:{child}" for child in removed)
            + f"; hierarchy -top {top}; proc; delete -output {top}/o:io_*; cutpoint "
            + " ".join(f"{top}/w:{cut}" for cut in cuts)
            + f"; expose {top}/w:proofTeletextDeadline {top}/w:proofTeletextUse; opt -purge; flatten; opt -purge; memory_map; opt; async2sync; opt_clean; check -assert; write_json {directory}/{name}.json; sat -set-assumes -set-init-zero {mode} -prove {target} 1 -verify -timeout 120 -show {target} -dump_json {witness}"
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
                s["name"] == target and "0" in s.get("wave", "")
                for s in json.loads(witness.read_text())["signal"]
            )
        )
        results.append({"name": name, "accepted": passed})
        if not passed:
            raise ValueError("Teletext memory deadline failed: " + name)
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
            "scope": "Actual parent RAM request/ready/capture scheduling precedes every slow-clock teletext input-latch acceptance. The actual latch driver guard and master-stage mapping are checked. Arbitrary accepted register writes and unrelated device outputs, initial reset only. Lost request/ready mutations require initialized counterexamples to actual input-use availability, not merely to auxiliary invariants.",
            "limits": "Free child event/CPU bus boundaries. Byte values, IC15/MA13/blanking interpretation, fast teletext tricks, reference phase alignment and whole-machine composition remain separate. No universal game equivalence.",
            "universal_game_equivalence": False,
        },
    )
    if not stable:
        raise ValueError("Teletext memory timing inputs changed")


if __name__ == "__main__":
    qualify()
