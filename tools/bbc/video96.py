"""Build the common 96 MHz video/system variant with exact 12 MHz dot enables."""

from __future__ import annotations

import fcntl
import json
import os
import re
import subprocess
from pathlib import Path

from tools.validation.common import ROOT, digest, write_json

from . import build, cpu_events

DIRECTORY = build.BBC / "full-video"


def generation_inputs():
    return build.generation_inputs()


def snapshot():
    return {
        "inputs": generation_inputs(),
        "outputs": build.hashes([*DIRECTORY.glob("*.v"), *DIRECTORY.glob("*.bin")]),
    }


def generate():
    cpu_events.prepare()
    DIRECTORY.mkdir(exist_ok=True)
    # Independent native test workers share this generated RTL. Serialize the
    # cache check and elaboration so a clean run starts only one sbt process.
    with (DIRECTORY / ".generation.lock").open("a") as lease:
        fcntl.flock(lease.fileno(), fcntl.LOCK_EX)
        return _generate_locked()


def _generate_locked():
    before = generation_inputs()
    receipt = DIRECTORY / "elaboration.json"
    try:
        cached = json.loads(receipt.read_text())
    except (OSError, ValueError):
        cached = {}
    state = snapshot()
    if (
        cached.get("inputs") != before
        or cached.get("outputs") != state["outputs"]
        or not state["outputs"]
    ):
        with (DIRECTORY / "generate.log").open("w") as log:
            subprocess.run(
                [
                    os.environ.get("SBT", "sbt"),
                    "runMain leet.bbc.GenerateSystem96",
                    "runMain leet.bbc.GenerateMachine96",
                    "runMain leet.bbc.GenerateVideo96",
                ],
                cwd=ROOT,
                stdout=log,
                stderr=subprocess.STDOUT,
                check=True,
                timeout=300,
            )
        if before != generation_inputs():
            raise ValueError("96 MHz generation inputs changed")
        for path in DIRECTORY.glob("*.v"):
            cpu_events.inline_file(path)
        state = snapshot()
        write_json(receipt, {**state, "clock_hz": 96000000, "physical_dot_hz": 12000000})
    return state


def component_snapshot():
    """Hash exactly the generated model and ROM files consumed by Verilator."""
    model = DIRECTORY / "BbcVideo96.v"
    files = [model]
    for name in re.findall(r'\$readmem[bh]\(\s*"([^"]+)"', model.read_text()):
        rom = (DIRECTORY / name).resolve()
        if not rom.is_relative_to(DIRECTORY.resolve()) or not rom.is_file():
            raise ValueError("Missing or external generated video ROM: " + name)
        files.append(rom)
    return build.hashes(set(files))


def compile(name, source, dependencies=(), *, savable=False, link_flags=()):
    generated = generate()
    directory = DIRECTORY / name
    directory.mkdir(exist_ok=True)
    binary = directory / "VBbcVideo96"
    sources = [Path(__file__), source, *dependencies]
    inputs = {
        # Whole-machine elaboration is still checked above. An unrelated VIA
        # or ADC correction does not require recompiling unchanged video C++.
        "generation": {"component_outputs": component_snapshot()},
        "sources": build.hashes(sources),
        "verilator": subprocess.check_output(["verilator", "--version"], text=True).strip(),
        "compiler": subprocess.check_output(["c++", "--version"], text=True).splitlines()[0],
        "recipe": "-O3 C++17 LTO; actual common-clock BbcVideo96; no state injection"
        + ("; savable own prefixes" if savable else ""),
        **({"link_flags": list(link_flags)} if link_flags else {}),
    }
    try:
        cached = json.loads((directory / "build.json").read_text())
    except (OSError, ValueError):
        cached = {}
    if (
        not binary.exists()
        or cached.get("inputs") != inputs
        or cached.get("binary") != digest(binary)
    ):
        with (directory / "build.log").open("w") as log:
            subprocess.run(
                [
                    "verilator",
                    "--cc",
                    "--exe",
                    "--build",
                    *(["--savable"] if savable else []),
                    "-j",
                    os.environ.get("VERILATOR_JOBS", "2"),
                    "-Wno-fatal",
                    "--top-module",
                    "BbcVideo96",
                    "-CFLAGS",
                    "-O3 -std=c++17 -flto -Werror=return-type",
                    "-LDFLAGS",
                    " ".join(["-flto", *link_flags]),
                    "-MAKEFLAGS",
                    "-B OPT_FAST=-O3",
                    "--Mdir",
                    str(directory),
                    str(DIRECTORY / "BbcVideo96.v"),
                    str(source),
                ],
                cwd=ROOT,
                stdout=log,
                stderr=subprocess.STDOUT,
                check=True,
                timeout=180,
            )
        if generated != snapshot() or inputs["sources"] != build.hashes(sources):
            raise ValueError("96 MHz probe inputs changed")
        write_json(directory / "build.json", {"inputs": inputs, "binary": digest(binary)})
    return binary, inputs


if __name__ == "__main__":
    from .interlace import qualify

    qualify(clock_multiplier=3)
    from .teletext_video import qualify as qualify_teletext

    qualify_teletext()
