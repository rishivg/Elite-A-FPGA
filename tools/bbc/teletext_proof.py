"""Prove packed teletext font storage preserves every cell output."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

from tools.validation.common import ROOT, write_json
from tools.validation.setup import verify

from .build import BBC, hashes
from .teletext import PROBES
from .teletext import qualify as qualify_cell


def qualify():
    verify()
    qualify_cell(next(probe for probe in PROBES if probe["name"] == "cells"))
    directory = BBC / "teletext-proof"
    directory.mkdir(parents=True, exist_ok=True)
    cell = BBC / "teletext-cells"
    sources = [
        Path(__file__),
        ROOT / "sim/formal/Saa5050CellBaseline.scala",
        *[
            ROOT / "rtl/video" / (name + ".scala")
            for name in ["Saa5050Cell", "TeletextAttributes", "TeletextGlyphRow", "TeletextFont"]
        ],
        ROOT / "build.sbt",
        ROOT / "project/build.properties",
        cell / "font.hex",
        cell / "Saa5050Cell.v",
        *cell.glob("Saa5050Cell.v_*.bin"),
    ]
    before = hashes(sources)
    with (directory / "generate.log").open("w") as log:
        subprocess.run(
            [
                os.environ.get("SBT", "sbt"),
                'set Compile / unmanagedSourceDirectories += file("sim/formal")',
                "runMain leet.video.GenerateSaa5050CellBaseline",
            ],
            cwd=ROOT,
            stdout=log,
            stderr=subprocess.STDOUT,
            check=True,
            timeout=300,
        )
    golden = directory / "golden/Saa5050CellBaseline.v"
    font = directory / "golden/Saa5050CellBaseline.v_toplevel_font.bin"
    # Keep only the baseline top; both use the same independently tested
    # attribute/glyph modules emitted with the production cell.
    gold = re.search(
        r"^module\s+Saa5050CellBaseline\b[\s\S]*?^endmodule\b", golden.read_text(), re.MULTILINE
    ).group()
    name = '"Saa5050CellBaseline.v_toplevel_font.bin"'
    if gold.count(name) != 1:
        raise ValueError("Unrecognized baseline font initialization")
    gold = gold.replace(name, '"' + str(font) + '"')
    rtl = directory / "equivalence.v"
    rtl.write_text(gold + "\n" + (cell / "Saa5050Cell.v").read_text())
    command = f"read_verilog -sv {rtl}; proc; memory; opt; flatten Saa5050CellBaseline Saa5050Cell; equiv_make Saa5050CellBaseline Saa5050Cell cell_equiv; hierarchy -top cell_equiv; async2sync; equiv_simple; equiv_induct -seq 2; equiv_status -assert"
    generated = hashes([golden, font, rtl])
    with (directory / "proof.log").open("w") as log:
        subprocess.run(
            ["yosys", "-p", command],
            cwd=cell,
            stdout=log,
            stderr=subprocess.STDOUT,
            check=True,
            timeout=180,
        )
    if before != hashes(sources) or generated != hashes([golden, font, rtl]):
        raise ValueError("Teletext proof inputs changed during execution")
    output = (directory / "proof.log").read_text()
    count = re.search(r"Of those cells (\d+) are proven and 0 are unproven", output)
    if count is None or "Equivalence successfully proven!" not in output:
        raise ValueError("Incomplete teletext cell equivalence proof")
    result = {
        "passed": True,
        "proven_cells": int(count[1]),
        "unproven_cells": 0,
        "inputs": before,
        "generated": generated,
        "yosys": subprocess.check_output(["yosys", "-V"], text=True).strip(),
        "command": command,
        "scope": "Every cell output, reset and unrestricted operations. Packed character words versus straightforward ten-row storage; no input assumptions. Shared attributes and glyph logic are checked separately against the unmodified original model.",
        "limits": "Preservation of font storage and row selection behavior, not video integration or physical chip timing.",
    }
    write_json(directory / "result.json", result)
    print(f"Teletext cell equivalence: {count[1]} proven cells; 0 unproven")
    return result


if __name__ == "__main__":
    qualify()
