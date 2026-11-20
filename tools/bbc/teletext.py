"""Check standalone SAA5050 attributes and glyph rows against the pinned original."""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from pathlib import Path

from tools.validation.common import ROOT, source_root, write_json
from tools.validation.setup import verify

from .build import BBC, hashes

PROBES = [
    {
        "name": "attributes",
        "class": "TeletextAttributes",
        "cpp": "bbc_teletext_attributes_test.cpp",
        "script": "reference_teletext_attributes.mjs",
        "counts": {"cases": 50, "observations": 515904},
        "expected": "Teletext attributes: 515904 observations in 50 cases; 0 differences",
        "coverage": "Four-byte pipeline; all bytes and ordered control-code pairs; held and separated graphics; colour, background, conceal, flashing, double-height rows; arbitrary input pin/clock interleavings and disabled clocks.",
    },
    {
        "name": "glyphs",
        "class": "TeletextGlyphRow",
        "cpp": "bbc_teletext_glyph_test.cpp",
        "script": "reference_teletext_glyphs.mjs",
        "counts": {"observations": 12288},
        "expected": "Teletext glyphs: 12288 observations; 0 differences",
        "coverage": "All three original glyph sets, every seven-bit character and every five-bit raster; alpha rounding, contiguous/separated mosaics and invalid-row/character blanking. Twelve physical dots are kept separate from sixteen resampled emulator weights.",
    },
    {
        "name": "cells",
        "class": "Saa5050Cell",
        "dependencies": ["TeletextAttributes", "TeletextGlyphRow", "TeletextFont"],
        "cpp": "bbc_teletext_cell_test.cpp",
        "script": "reference_teletext_attributes.mjs",
        "counts": {"cases": 50, "observations": 515904},
        "expected": "Teletext cells: 515904 observations in 50 cases; 0 differences",
        "coverage": "Combined character pipeline, font ROM, rounded alpha and mosaics, held glyphs, conceal, flash and double-height blanking after every pin/character-clock operation. Font rows are unchanged source data, not expected rendered pixels.",
    },
]


def inputs(probe):
    vendor = source_root("jsbeeb") / "src"
    return {
        "sources": hashes(
            [
                Path(__file__),
                ROOT / "rtl/video" / (probe["class"] + ".scala"),
                *[ROOT / "rtl/video" / (name + ".scala") for name in probe.get("dependencies", [])],
                ROOT / "sim/native" / probe["cpp"],
                ROOT / "tools/bbc" / probe["script"],
                ROOT / "tools/validation/reference.lock.json",
                ROOT / "build.sbt",
                ROOT / "project/build.properties",
                *[
                    vendor / name
                    for name in ["teletext.js", "teletext_data.js", "bbc-palette.js", "binary.js"]
                ],
            ]
        ),
        "verilator": subprocess.check_output(["verilator", "--version"], text=True).strip(),
        "compiler": subprocess.check_output(["c++", "--version"], text=True).splitlines()[0],
        "recipe": {
            "optimization": "-O3 -std=c++17 -flto -Werror=return-type; OPT_FAST=-O3",
            **probe["counts"],
        },
    }


def qualify(probe):
    directory = BBC / ("teletext-" + probe["name"])
    directory.mkdir(parents=True, exist_ok=True)
    before = inputs(probe)
    rtl = directory / (probe["class"] + ".v")
    fixture = directory / (probe["name"] + ".bin")
    binary = directory / "native" / ("V" + probe["class"])

    def artifact_paths():
        return [
            rtl,
            fixture,
            binary,
            *directory.glob(probe["class"] + ".v_*.bin"),
            *([directory / "font.hex"] if probe["name"] == "cells" else []),
        ]

    artifacts = artifact_paths()
    try:
        cached = json.loads((directory / "build.json").read_text())
    except (OSError, ValueError):
        cached = {}
    if (
        cached.get("inputs") != before
        or not all(path.is_file() for path in artifacts)
        or cached.get("artifacts") != hashes(artifacts)
    ):
        original = subprocess.run(
            [
                "node",
                str(ROOT / "tools/bbc" / probe["script"]),
                str(source_root("jsbeeb")),
                str(fixture),
                *(["cells", str(directory / "font.hex")] if probe["name"] == "cells" else []),
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
            timeout=60,
        )
        if json.loads(original.stdout) != probe["counts"]:
            raise ValueError("Incomplete independent teletext corpus")
        corpus_inputs = hashes(
            [fixture, *([directory / "font.hex"] if probe["name"] == "cells" else [])]
        )
        with (directory / "generate.log").open("w") as log:
            subprocess.run(
                [os.environ.get("SBT", "sbt"), "runMain leet.video.Generate" + probe["class"]],
                cwd=ROOT,
                stdout=log,
                stderr=subprocess.STDOUT,
                check=True,
                timeout=300,
            )
        with (directory / "build.log").open("w") as log:
            subprocess.run(
                [
                    "verilator",
                    "--cc",
                    "--exe",
                    "--build",
                    "-j",
                    os.environ.get("VERILATOR_JOBS", "4"),
                    "-Wno-fatal",
                    "--top-module",
                    probe["class"],
                    "-CFLAGS",
                    "-O3 -std=c++17 -flto -Werror=return-type",
                    "-LDFLAGS",
                    "-flto",
                    "-MAKEFLAGS",
                    "-B OPT_FAST=-O3",
                    "--Mdir",
                    str(directory / "native"),
                    str(rtl),
                    str(ROOT / "sim/native" / probe["cpp"]),
                ],
                cwd=ROOT,
                stdout=log,
                stderr=subprocess.STDOUT,
                check=True,
                timeout=120,
            )
        artifacts = artifact_paths()
        if inputs(probe) != before or corpus_inputs != hashes(
            [ROOT / name for name in corpus_inputs]
        ):
            raise ValueError("Teletext sources changed during compilation")
        write_json(directory / "build.json", {"inputs": before, "artifacts": hashes(artifacts)})
    snapshot = hashes(artifacts)
    started = time.monotonic()
    # Spinal's ROM initialization files are relative to the generated module.
    result = subprocess.run(
        [str(binary), str(fixture)], cwd=directory, capture_output=True, text=True, timeout=60
    )
    elapsed = time.monotonic() - started
    print(result.stdout.strip())
    if result.returncode:
        print(result.stderr.strip())
    result.check_returncode()
    expected = probe["expected"]
    if re.fullmatch(re.escape(expected) + r"\s*", result.stdout) is None:
        raise ValueError("Incomplete teletext comparison")
    if before != inputs(probe) or snapshot != hashes(artifacts):
        raise ValueError("Teletext sources or artifacts changed during comparison")
    receipt = {
        "passed": True,
        **probe["counts"],
        "differences": 0,
        "elapsed_seconds": elapsed,
        "inputs": before,
        "artifacts": snapshot,
        "coverage": probe["coverage"],
        "scope": "Standalone component compared with public methods and original glyph tables of the pinned, unmodified jsbeeb Teletext class. No reference state is injected into RTL.",
        "limits": "Standalone cell scope only. The optional 96 MHz BbcVideo integration is checked separately; video-bus pipeline timing and physical SAA5050 behavior are not established by this corpus.",
    }
    write_json(directory / "result.json", receipt)
    return receipt


if __name__ == "__main__":
    verify()
    for probe in PROBES:
        qualify(probe)
