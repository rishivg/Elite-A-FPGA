"""Prove video optimizations against the earlier straightforward register bank."""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
from pathlib import Path

from tools.validation.common import ROOT, digest, write_json

from . import baselines, build
from .probe import device_rtl

BASELINE = "9936dbdcd92578e483b7cd130dd78ce9b26d29fe"
DIRECTORY = build.BBC / "video-proof"


def historical(path):
    return baselines.historical(BASELINE, path)


def replace_once(text, before, after):
    if text.count(before) != 1:
        raise ValueError("Unrecognized video baseline: " + before)
    return text.replace(before, after)


def qualify():
    if not build.generation_current():
        raise ValueError("Run make bbc-gen before proving current video RTL")
    before = device_rtl(["BbcVideo", "Crtc6845", "BbcVideoAddress"])
    sources = DIRECTORY / "sources"
    sources.mkdir(parents=True, exist_ok=True)
    historical_video = historical("rtl/bbc/BbcVideo.scala")
    current_video = (ROOT / "rtl/bbc/BbcVideo.scala").read_text()
    # This is an optimization preservation proof. Share intentional cursor,
    # light-pen, external-RA and address-wiring behavior, each checked separately.
    # Only the register-write mux and palette storage come from the pinned
    # straightforward baseline. No input assumptions or state injection.
    video = replace_once(current_video, "case class BbcVideo(", "case class VideoBaseline(")
    start = "  val writableRegs = "
    end = "  val index = "
    old_start = "  val regs = "
    old_end = "  val index = "
    old_registers = historical_video[
        historical_video.index(old_start) : historical_video.index(old_end)
    ]
    video = replace_once(video, video[video.index(start) : video.index(end)], old_registers)
    start = "  // Valid bits preserve "
    end = "  when(io.crtcWrite) {"
    old_start = "  val palette = Vec.fill(16)"
    old_end = "  when(io.crtcWrite) {"
    old_palette = historical_video[
        historical_video.index(old_start) : historical_video.index(old_end)
    ]
    video = replace_once(video, video[video.index(start) : video.index(end)], old_palette)
    start = "  when(io.crtcWrite) {"
    end = "  io.result := 0"
    old_bus = historical_video[historical_video.index(start) : historical_video.index(end)]
    old_bus = replace_once(old_bus, ".elsewhen(index < 18)", ".elsewhen(index < 16)")
    video = replace_once(video, video[video.index(start) : video.index(end)], old_bus)
    video = replace_once(video, "  io.palette := paletteMirror", "  io.palette := palette.asBits")
    video = replace_once(
        video,
        "  when(io.lightPenPulse) { lightPen := crtc.io.busAddress }",
        "  when(io.lightPenPulse) {\n    regs(16) := crtc.io.busAddress(13 downto 8).asBits.resize(8)\n    regs(17) := crtc.io.busAddress(7 downto 0).asBits\n  }",
    )
    video = replace_once(
        video,
        "  val colour = Mux(paletteValid(colourIndex), palette.readAsync(colourIndex), B(0, 4 bits))",
        "  val colour = palette(colourIndex)",
    )
    (sources / "VideoBaseline.scala").write_text(video)
    for obsolete in [sources / "CrtcBaseline.scala"]:
        obsolete.unlink(missing_ok=True)
    (sources / "GenerateVideoBaseline.scala").write_text(
        "package leet.bbc\nimport spinal.core._\nobject GenerateVideoBaseline extends App {\n"
        '  SpinalConfig(targetDirectory = "build/bbc/video-proof/golden",\n'
        "    defaultConfigForClockDomains = ClockDomainConfig(resetActiveLevel = HIGH))\n"
        "    .generateVerilog(VideoBaseline())\n}\n"
    )
    snapshot = build.hashes(
        [
            *baselines.dependencies(BASELINE),
            Path(__file__),
            ROOT / "rtl/bbc/BbcVideo.scala",
            *sources.glob("*.scala"),
        ]
    )
    with (DIRECTORY / "generate.log").open("w") as log:
        subprocess.run(
            [
                os.environ.get("SBT", "sbt"),
                f'set Compile / unmanagedSourceDirectories += file("{sources}")',
                "runMain leet.bbc.GenerateVideoBaseline",
            ],
            cwd=ROOT,
            stdout=log,
            stderr=subprocess.STDOUT,
            check=True,
            timeout=300,
        )
    gold = re.search(
        r"^module\s+VideoBaseline\b[\s\S]*?^endmodule\b",
        (DIRECTORY / "golden/VideoBaseline.v").read_text(),
        re.MULTILINE,
    ).group()
    # Generated _zz helper names can collide for unrelated expressions.
    # Rename those baseline wires; keep all state names and every output.
    gold = re.sub(r"\b_zz_\w+\b", lambda match: "baseline" + match[0], gold)
    pattern = r"^module\s+BbcVideo\b[\s\S]*?^endmodule\b"
    gate = re.search(pattern, before, re.MULTILINE).group()
    # Expose the relation between RAM contents, validity and the old 16 palette
    # registers. These are read-only proof wires: no state, assumptions or
    # drivers are added. The formal tool must prove all 64 relation bits too.
    invariant = "\n".join(
        f"  (* keep *) wire [3:0] palette_{i} = paletteValid[{i}] ? palette[{i}] : 4'b0;"
        for i in range(16)
    )
    gate = gate.replace("endmodule", invariant + "\nendmodule")
    crtc_module = re.search(
        r"^module\s+Crtc6845\b[\s\S]*?^endmodule\b", before, re.MULTILINE
    ).group()
    rtl = DIRECTORY / "equivalence.v"
    address_module = re.search(
        r"^module\s+BbcVideoAddress\b[\s\S]*?^endmodule\b", before, re.MULTILINE
    ).group()
    rtl.write_text(gold + "\n" + gate + "\n" + crtc_module + "\n" + address_module + "\n")
    command = (
        f"read_verilog -sv {rtl}; proc; memory; opt; flatten VideoBaseline BbcVideo; "
        "equiv_make VideoBaseline BbcVideo video_equiv; hierarchy -top video_equiv; "
        "async2sync; equiv_simple; equiv_induct -seq 2; equiv_status -assert"
    )
    with (DIRECTORY / "proof.log").open("w") as log:
        subprocess.run(
            ["yosys", "-p", command],
            cwd=ROOT,
            stdout=log,
            stderr=subprocess.STDOUT,
            check=True,
            timeout=120,
        )
    if (
        not build.generation_current()
        or before != device_rtl(["BbcVideo", "Crtc6845", "BbcVideoAddress"])
        or snapshot
        != build.hashes(
            [
                *baselines.dependencies(BASELINE),
                Path(__file__),
                ROOT / "rtl/bbc/BbcVideo.scala",
                *sources.glob("*.scala"),
            ]
        )
    ):
        raise ValueError("Video RTL changed during proof")
    output = (DIRECTORY / "proof.log").read_text()
    count = re.search(r"Of those cells (\d+) are proven and 0 are unproven", output)
    if count is None or "Equivalence successfully proven!" not in output:
        raise ValueError("Incomplete video equivalence proof")
    write_json(
        DIRECTORY / "result.json",
        {
            "passed": True,
            "proven_cells": int(count[1]),
            "unproven_cells": 0,
            "baseline_commit": BASELINE,
            "historical_source_sha256": hashlib.sha256(historical_video.encode()).hexdigest(),
            "yosys": subprocess.check_output(["yosys", "-V"], text=True).strip(),
            "inputs": build.hashes(
                [
                    *baselines.dependencies(BASELINE),
                    Path(__file__),
                    *sources.glob("*.scala"),
                    rtl,
                    DIRECTORY / "golden/VideoBaseline.v",
                ]
            ),
            "generated_model": digest(build.BBC / "BbcMachine.v"),
            "command": command,
            "scope": "All default 32 MHz video outputs with unrestricted inputs, reset and proved palette relations. The pinned historical register mux and palette flop layouts replace only their optimized production sections. Current CRTC, external RA, memory mapping, cursor and light-pen behavior are shared; separate independent probes establish those intentional additions.",
            "limits": "Optimization preservation only, with intentional frontend/cursor/light-pen behavior shared. Does not independently prove those additions, physical timing or the 96 MHz teletext path.",
        },
    )
    print(f"Video equivalence: {count[1]} proven cells; 0 unproven")


if __name__ == "__main__":
    qualify()
