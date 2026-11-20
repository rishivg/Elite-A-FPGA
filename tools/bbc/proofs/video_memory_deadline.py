"""Prove actual parent video fetch/capture precedes first bitmap pixel use."""

import re
import shutil
import subprocess
from pathlib import Path

from tools.bbc import build, bus_binding_proof, video96
from tools.bbc.proofs.video_cursor import hashes, replace_once
from tools.validation.common import ROOT, write_json


def observe(source, top):
    parent = bus_binding_proof.module(source, top)
    video = bus_binding_proof.module(source, "BbcVideo")
    ram = bus_binding_proof.module(source, "BbcRam")
    guards = re.findall(r"if\(([^()]*)\) begin\s*videoResponse <= data;", ram)
    if len(guards) != 1:
        raise ValueError("Changed actual video response-capture binding")
    header = video.index(");")
    video = (
        video[:header]
        + ", output wire [4:0] proofMaster, output wire proofClock, output wire [3:0] proofPosition\n"
        + video[header:]
    )
    video = (
        video.removesuffix("endmodule")
        + "assign proofMaster=master;assign proofClock=clock32;assign proofPosition=physicalPixel;\nendmodule"
    )
    header = ram.index(");")
    ram = ram[:header] + ", output wire proofVideoReady\n" + ram[header:]
    ram = ram.removesuffix("endmodule") + f"assign proofVideoReady={guards[0]};\nendmodule"
    for kind, instance, connections in (
        (
            "BbcVideo",
            "video",
            ".proofMaster(proofMaster),.proofClock(proofClock),.proofPosition(proofPosition)",
        ),
        ("BbcRam", "ram", ".proofVideoReady(proofReady)"),
    ):
        match = re.search(r"\b" + kind + r"\s+" + instance + r"\s*\([\s\S]*?\n\s*\);", parent)
        if match is None:
            raise ValueError("Changed actual video/RAM instance")
        parent = replace_once(
            parent, match[0], match[0].removesuffix(");") + "," + connections + ");"
        )
    removed, cuts = [], {"cpu_address", "cpu_write_enable", "cpu_write_data"}
    for kind, instance in re.findall(r"^\s+(\w+)\s+(\w+)\s*\(\s*$", parent, re.M):
        if kind in {"BbcAddressDecode", "BbcVideo", "BbcRam"}:
            continue
        removed.append(instance)
        if kind in {"BbcCpu", "BbcCpuEvents"}:
            continue
        child = bus_binding_proof.module(source, kind)
        outputs = re.findall(
            r"output\s+(?:wire|reg)\s*(?:\[[^\]]+\]\s*)?(\w+)", child.split(");", 1)[0]
        )
        ports = bus_binding_proof.ports(parent, kind, instance)
        for output in outputs:
            net = re.sub(r"\[[^\]]*\]", "", ports[output]).strip()
            if not re.fullmatch(r"\w+", net):
                raise ValueError("Changed abstracted child output")
            cuts.add(net)
    addition = """
wire [4:0] proofMaster;
wire proofClock,proofReady;
wire [3:0] proofPosition;
wire firstPixel=proofClock && proofMaster[0] && proofPosition==0;
reg requested=0,captured=0;
initial assume(reset);
always @(posedge clk or posedge reset)
 if(reset)begin requested<=0;captured<=0;end
 else begin
  if(video_io_memoryRead)requested<=1;
  if(proofReady)captured<=1;
  if(firstPixel)begin requested<=0;captured<=0;end
 end
wire firstPhase=proofMaster==17 || (video_io_control[4] && proofMaster==1);
wire proofVideoUse=reset || video_io_control[1] ||
 !(video_io_pixel && proofPosition==0 && (!requested || !(captured || proofReady)));
wire proofVideoDeadline=reset || (
 proofMaster==(phase_1+5'd16) && proofClock==clock32 &&
 (video_io_control[1] || video_io_pixel==(proofClock && proofMaster[0])) &&
 requested==firstPhase && captured==(requested && !proofReady) &&
 proofVideoUse);
"""
    return (
        replace_once(
            replace_once(
                replace_once(source, bus_binding_proof.module(source, "BbcVideo"), video),
                bus_binding_proof.module(source, "BbcRam"),
                ram,
            ),
            bus_binding_proof.module(source, top),
            parent.removesuffix("endmodule") + addition + "endmodule",
        ),
        removed,
        sorted(cuts),
    )


def qualify():
    if not build.generation_current():
        raise ValueError("Current actual parents required")
    directory = build.BBC / "video-memory-response-deadline-proof"
    directory.mkdir(exist_ok=True)
    sources = [
        (1, build.BBC / "BbcMachine.v", "BbcMachine"),
        (3, video96.DIRECTORY / "BbcMachine96.v", "BbcMachine96"),
    ]
    files = [
        Path(__file__),
        Path(bus_binding_proof.__file__),
        Path(__file__).with_name("video_cursor.py"),
        ROOT / "rtl/bbc/BbcMachine.scala",
        ROOT / "rtl/bbc/BbcRam.scala",
        ROOT / "rtl/bbc/BbcVideo.scala",
        *[path for _, path, _ in sources],
        *build.BBC.glob("*.bin"),
        *video96.DIRECTORY.glob("*.bin"),
        Path(shutil.which("yosys")),
    ]
    before = hashes(files)
    clocks = []
    for multiplier, path, top in sources:
        source = path.read_text()
        variants = (
            ("positive", source),
            (
                "lost-fetch",
                replace_once(
                    source,
                    "assign io_memoryRead = (clock32 && (control[4] ? (master[3 : 0] == 4'b0000) : (master == 5'h10)));",
                    "assign io_memoryRead = 1'b0;",
                ),
            ),
            (
                "lost-ready",
                replace_once(source, "videoReady <= io_videoEnable;", "videoReady <= 1'b0;"),
            ),
        )
        rows = []
        for name, text in variants:
            observed, removed, cuts = observe(text, top)
            rtl = directory / f"{name}{multiplier}.v"
            rtl.write_text(observed)
            witness = directory / f"{name}{multiplier}-counterexample.json"
            proof = (
                "-tempinduct -initsteps 4 -maxsteps 16"
                if name == "positive"
                else f"-seq {24 * multiplier + 8}"
            )
            target = "proofVideoDeadline" if name == "positive" else "proofVideoUse"
            command = (
                f"read_verilog -formal -D SYNTHESIS {rtl}; delete "
                + " ".join(f"{top}/c:{child}" for child in removed)
                + f"; hierarchy -top {top}; proc; delete -output {top}/o:io_*; cutpoint "
                + " ".join(f"{top}/w:{cut}" for cut in cuts)
                + f"; expose {top}/w:proofVideoDeadline {top}/w:proofVideoUse; opt -purge; flatten; opt -purge; memory_map; opt; async2sync; opt_clean; check -assert; write_json {directory}/{name}{multiplier}.json; sat -set-assumes -set-init-zero {proof} -prove {target} 1 -verify -timeout 120 -show {target} -dump_json {witness}"
            )
            log = directory / f"{name}{multiplier}.log"
            with log.open("w") as output:
                process = subprocess.run(
                    ["yosys", "-Q", "-T", "-p", command],
                    cwd=path.parent,
                    stdout=output,
                    stderr=subprocess.STDOUT,
                    timeout=300,
                )
            accepted = (
                (process.returncode == 0 and "Induction step proven: SUCCESS!" in log.read_text())
                if name == "positive"
                else (
                    process.returncode != 0
                    and "proof did fail!" in log.read_text()
                    and witness.is_file()
                )
            )
            rows.append({"name": name, "accepted": accepted})
            if not accepted:
                raise ValueError(f"Video memory deadline failed: {name}/{multiplier}")
        clocks.append({"clock_multiplier": multiplier, "checks": rows})
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
            "clocks": clocks,
            "scope": "Actual parent CPU scheduling, ULA control-write decoder, video master/divider, fetch strobes and RAM ready/capture guard retained at both clocks. Every first bitmap character pixel has a preceding fetch and an available response. Read-only request/capture monitors and phase relations strengthen induction; arbitrary CPU bus data/addresses/direction, later reset histories and abstracted unrelated device outputs. Actual lost-fetch and lost-ready mutations require initialized counterexamples at both rates.",
            "limits": "Response scheduling only, not memory byte values, CRTC/reference counter alignment, colour decoding, teletext consumption or universal game equivalence. RAM contents/retention and video decoding have separate contracts.",
            "universal_game_equivalence": False,
        },
    )
    if not stable:
        raise ValueError("Video memory deadline inputs changed")


if __name__ == "__main__":
    qualify()
