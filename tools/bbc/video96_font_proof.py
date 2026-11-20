"""Prove clocked font integration preserves all 96 MHz video outputs."""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

from tools.validation.common import ROOT, write_json

from . import baselines, build, video96

BASELINE = "4abd5fa8576553ccb2cc61a136bbecee090c72d3"
OLD_COLUMN = "val column = (cellHorizontal - skew.resize(8) - 2).resize(11)"
CORRECTED_COLUMN = "val column = cellHorizontal.resize(11) - skew.resize(11) - U(2, 11 bits)"
SHARED = [
    "rtl/bbc/Crtc6845.scala",
    "rtl/bbc/BbcVideoAddress.scala",
    "rtl/video/TeletextAttributes.scala",
    "rtl/video/TeletextGlyphRow.scala",
]
CHILDREN = ["Crtc6845", "BbcVideoAddress", "TeletextAttributes", "TeletextGlyphRow"]


def historical(path):
    return baselines.historical(BASELINE, path)


def align_dot_phase(text):
    """Align an anonymous register's name; its equivalence still needs proof."""
    counters = re.findall(r"reg\s+\[2:0\]\s+(_zz_when_BbcVideo\w*_l\d+)\s*;", text)
    if len(counters) != 1:
        raise ValueError("Unrecognized teletext dot-phase register")
    counter = re.escape(counters[0])
    for pattern in [
        rf"{counter}\s*==\s*3'b111",
        rf"{counter}\s*<=\s*\({counter}\s*\+\s*3'b001\)",
        rf"{counter}\s*<=\s*3'b000",
    ]:
        if not re.search(pattern, text):
            raise ValueError("Unrecognized dot-phase update/reset")
    return re.sub(r"\b" + counter + r"\b", "teletextDotPhase", text)


def relation_blacklist(gold, gate):
    """Exclude only internal combinational relation points, never ports."""
    registered = []
    for model in [gold, gate]:
        bits = set()
        for cell in model["cells"].values():
            if cell["type"] in {"$dff", "$adff", "$sdff", "$dffe", "$adffe", "$sdffe"}:
                bits.update(bit for bit in cell["connections"]["Q"] if isinstance(bit, int))
        registered.append(bits)
    ports = set(gold["ports"]) | set(gate["ports"])
    names = []
    for name in sorted(set(gold["netnames"]) & set(gate["netnames"])):
        if name in ports:
            continue
        nets = [
            {bit for bit in model["netnames"][name]["bits"] if isinstance(bit, int)}
            for model in [gold, gate]
        ]
        if not all(bits and bits <= state for bits, state in zip(nets, registered)):
            names.append(name)
    return names


def run_yosys(directory, name, command):
    with (directory / name).open("w") as log:
        subprocess.run(
            ["yosys", "-Q", "-T", "-p", command],
            cwd=directory,
            stdout=log,
            stderr=subprocess.STDOUT,
            check=True,
            timeout=300,
        )


def qualify():
    video96.generate()
    directory = build.BBC / "video96-font-proof"
    sources = directory / "sources"
    sources.mkdir(parents=True, exist_ok=True)
    # Keep shared logic byte-identical to the pinned baseline. This proof is
    # about font storage/integration, not changes to attributes or the CRTC.
    for path in SHARED:
        if (ROOT / path).read_text() != historical(path):
            raise ValueError("Shared video baseline changed: " + path)
    for path, name in [
        ("rtl/bbc/BbcVideo.scala", "BbcVideo"),
        ("rtl/video/Saa5050Cell.scala", "Saa5050Cell"),
    ]:
        baseline = historical(path)
        if name == "BbcVideo":
            if baseline.count(OLD_COLUMN) != 1:
                raise ValueError("Unrecognized historical teletext coordinate expression")
            # The independent original-C border comparison checks this correction.
            # Both sides here use it, so this proof isolates font integration.
            baseline = baseline.replace(OLD_COLUMN, CORRECTED_COLUMN)
        text = re.sub(r"\b" + name + r"\b", name + "FontBaseline", baseline)
        text = re.sub(r"\bGenerate" + name + r"\b", "Generate" + name + "FontBaseline", text)
        if name == "BbcVideo":
            text = text.replace("leet.video.Saa5050Cell(", "leet.video.Saa5050CellFontBaseline(")
        (sources / (name + "FontBaseline.scala")).write_text(text)
    (sources / "GenerateFontBaseline.scala").write_text(
        "package leet.bbc\nimport spinal.core._\nobject GenerateFontBaseline extends App {\n"
        '  SpinalConfig(targetDirectory = "build/bbc/video96-font-proof/golden",\n'
        "    defaultConfigForClockDomains = ClockDomainConfig(resetActiveLevel = HIGH))\n"
        "    .generateVerilog(BbcVideoFontBaseline(3))\n}\n"
    )
    dependencies = [
        *baselines.dependencies(BASELINE),
        Path(__file__),
        *sources.glob("*.scala"),
        *[ROOT / p for p in SHARED],
    ]
    before = build.hashes(dependencies)
    generated = video96.snapshot()
    with (directory / "generate.log").open("w") as log:
        subprocess.run(
            [
                os.environ.get("SBT", "sbt"),
                f'set Compile / unmanagedSourceDirectories += file("{sources}")',
                "runMain leet.bbc.GenerateFontBaseline",
            ],
            cwd=ROOT,
            stdout=log,
            stderr=subprocess.STDOUT,
            check=True,
            timeout=300,
        )
    artifacts = []
    models = [
        ("gold", "BbcVideoFontBaseline", directory / "golden"),
        ("gate", "BbcVideo96", video96.DIRECTORY),
    ]
    for label, top, location in models:
        path = location / (top + ".v")
        modules = {
            match[1]: match[0]
            for match in re.finditer(
                r"^module\s+(\w+)\b[\s\S]*?^endmodule\b", path.read_text(), re.M
            )
        }
        names = [top, *CHILDREN]
        names += ["Saa5050CellFontBaseline"] if label == "gold" else ["Saa5050Cell", "TeletextFont"]
        text = align_dot_phase("\n".join(modules[name] for name in names))
        # Renaming the baseline Scala type also changes Spinal's instance
        # name. Restore its production name so shared register relations
        # can be proved individually instead of an enormous combined cone.
        if label == "gold":
            text = re.sub(r"\bsaa5050CellFontBaseline_1\b", "saa5050Cell_1", text)
        for filename in re.findall(r'\$readmemb\("([^"]+)"', text):
            memory = location / filename
            if not memory.is_file():
                raise ValueError("Missing font initialization: " + str(memory))
            text = text.replace('"' + filename + '"', '"' + str(memory) + '"')
            artifacts.append(memory)
        artifacts.append(path)
        (directory / (label + ".v")).write_text(text + "\n")
        command = (
            f"read_verilog -sv {label}.v; hierarchy -top {top}; proc; memory; opt; "
            f"flatten; opt; rename {top} Video{label}; write_rtlil {label}.il; "
            f"write_json {label}.json"
        )
        run_yosys(directory, label + "-prepare.log", command)
    model_json = [
        json.loads((directory / (label + ".json")).read_text())["modules"]["Video" + label]
        for label in ["gold", "gate"]
    ]
    signatures = [
        {name: (port["direction"], len(port["bits"])) for name, port in model["ports"].items()}
        for model in model_json
    ]
    if signatures[0] != signatures[1]:
        raise ValueError("Video proof interfaces differ")
    (directory / "unmatched-intermediates.txt").write_text(
        "\n".join(relation_blacklist(*model_json)) + "\n"
    )
    artifacts += [
        directory / filename
        for filename in ["gold.v", "gate.v", "gold.il", "gate.il", "unmatched-intermediates.txt"]
    ]
    snapshot = build.hashes(artifacts)
    command = (
        "read_rtlil gold.il gate.il; "
        "equiv_make -blacklist unmatched-intermediates.txt Videogold Videogate video_equiv; "
        "hierarchy -top video_equiv; async2sync; equiv_simple; equiv_induct -seq 8; "
        "equiv_status -assert"
    )
    run_yosys(directory, "proof.log", command)
    if (
        before != build.hashes(dependencies)
        or generated != video96.snapshot()
        or snapshot != build.hashes(artifacts)
    ):
        raise ValueError("Video font proof inputs changed during execution")
    output = (directory / "proof.log").read_text()
    count = re.search(r"Of those cells (\d+) are proven and 0 are unproven", output)
    if count is None or "Equivalence successfully proven!" not in output:
        raise ValueError("Incomplete video font preservation proof")
    result = {
        "passed": True,
        "proven_cells": int(count[1]),
        "unproven_cells": 0,
        "baseline_commit": BASELINE,
        "baseline_coordinate_correction": {"before": OLD_COLUMN, "after": CORRECTED_COLUMN},
        "inputs": before,
        "generation": generated,
        "artifacts": snapshot,
        "yosys": subprocess.check_output(["yosys", "-V"], text=True).strip(),
        "command": command,
        "scope": "Every BbcVideo96 output, reset and unrestricted operations. Common registered state is proved; internal combinational relation points are excluded because font data intentionally arrives one master edge later. No input assumptions or output exclusions.",
        "limits": "Font integration preservation against the pinned earlier video implementation with the same explicit teletext border-coordinate correction on both sides. The correction itself requires independent pixel comparison. Physical timing remains separate.",
    }
    write_json(directory / "result.json", result)
    print(f"Video96 font preservation: {count[1]} proven cells; 0 unproven")
    return result


if __name__ == "__main__":
    qualify()
