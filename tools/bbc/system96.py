"""Build the actual 96 MHz machine for the original-game replay runner."""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

from tools.validation.common import ROOT, digest, write_json

from . import build, video96

DIRECTORY = video96.DIRECTORY / "native"
RECEIPT = DIRECTORY / "build.json"
BINARY = build.native_binary(3)


def idle_falling_edge(directory):
    """Recognize the generated model whose clock only feeds one rising-edge trigger."""
    clock_uses = []
    for source in directory.glob("VBbcMachine96*.cpp"):
        for line in source.read_text().splitlines():
            if re.search(r"\bclk\b|\b__Vtrigprevexpr___TOP__clk__0\b", line):
                clock_uses.append(line.strip())
    return sorted(clock_uses) == sorted(
        [
            "vlSelfRef.__VactTriggered.set(0U, ((IData)(vlSelfRef.clk)",
            "& (~ (IData)(vlSelfRef.__Vtrigprevexpr___TOP__clk__0))));",
            "vlSelfRef.__Vtrigprevexpr___TOP__clk__0 = vlSelfRef.clk;",
            "vlSelfRef.__Vtrigprevexpr___TOP__clk__0 = vlSelfRef.clk;",
            "if (VL_UNLIKELY((vlSelfRef.clk & 0xfeU))) {",
            'Verilated::overWidthError("clk");}',
            "VL_DBG_MSGF(\"         'act' region trigger index 0 is active: @(posedge clk)\\n\");",
            "VL_DBG_MSGF(\"         'nba' region trigger index 0 is active: @(posedge clk)\\n\");",
            "vlSelf->clk = VL_RAND_RESET_I(1);",
            "vlSelf->__Vtrigprevexpr___TOP__clk__0 = VL_RAND_RESET_I(1);",
            ", clk{vlSymsp->TOP.clk}",
            "os<<clk;",
            "os<<__Vtrigprevexpr___TOP__clk__0;",
            "os>>clk;",
            "os>>__Vtrigprevexpr___TOP__clk__0;",
        ]
    )


def configure_clock_header(directory):
    header = directory / "BbcMachine96IdleFall.h"
    header.unlink(missing_ok=True)
    supported = idle_falling_edge(directory)
    if supported:
        header.write_text('#include "VBbcMachine96___024root.h"\n#define BBC_IDLE_FALL 1\n')
    return supported


def inputs():
    return {
        "generation": video96.snapshot(),
        "sources": build.hashes(
            [
                ROOT / "sim/native/bbc_machine96.cpp",
                ROOT / "sim/native/bbc.cpp",
                ROOT / "sim/native/bbc_audio.h",
                ROOT / "sim/native/bbc_fm_media.h",
                ROOT / "sim/native/bbc_disc_medium.h",
                ROOT / "sim/native/bbc_frame_hash.h",
                ROOT / "rtl/bbc/BbcCpu.v",
                build.BBC / "ArletCpuEnabled.v",
                ROOT / "third_party/6502/ALU.v",
                Path(__file__),
            ]
        ),
        "crypto": subprocess.check_output(
            ["pkg-config", "--modversion", "libcrypto"], text=True
        ).strip(),
        "verilator": subprocess.check_output(["verilator", "--version"], text=True).strip(),
        "compiler": subprocess.check_output(["c++", "--version"], text=True).splitlines()[0],
        "recipe": "-O3 C++17 LTO; savable BbcMachine96; guarded idle falling edges; 32 MHz fixture units",
    }


def current():
    try:
        saved = json.loads(RECEIPT.read_text())
        generated = json.loads((video96.DIRECTORY / "elaboration.json").read_text())
        return (
            generated.get("inputs") == video96.generation_inputs()
            and generated.get("outputs") == video96.snapshot()["outputs"]
            and BINARY.is_file()
            and saved.get("inputs") == inputs()
            and saved.get("binary") == digest(BINARY)
        )
    except (OSError, ValueError):
        return False


def main():
    video96.generate()
    DIRECTORY.mkdir(exist_ok=True)
    if current():
        print("Verified unchanged 96 MHz machine; reusing simulator", flush=True)
        return
    before = inputs()
    with (DIRECTORY / "build.log").open("w") as log:
        subprocess.run(
            [
                "verilator",
                "--cc",
                "--exe",
                "--savable",
                "-Wno-fatal",
                "--top-module",
                "BbcMachine96",
                "-CFLAGS",
                "-O3 -std=c++17 -flto",
                "-LDFLAGS",
                "-flto -lcrypto",
                "--Mdir",
                str(DIRECTORY),
                str(video96.DIRECTORY / "BbcMachine96.v"),
                str(ROOT / "rtl/bbc/BbcCpu.v"),
                str(build.BBC / "ArletCpuEnabled.v"),
                str(ROOT / "third_party/6502/ALU.v"),
                str(ROOT / "sim/native/bbc_machine96.cpp"),
            ],
            cwd=ROOT,
            stdout=log,
            stderr=subprocess.STDOUT,
            check=True,
            timeout=300,
        )
        fast_falling_edge = configure_clock_header(DIRECTORY)
        subprocess.run(
            [
                "make",
                "-f",
                "VBbcMachine96.mk",
                "-B",
                "-j",
                os.environ.get("VERILATOR_JOBS", "2"),
                "OPT_FAST=-O3",
            ],
            cwd=DIRECTORY,
            stdout=log,
            stderr=subprocess.STDOUT,
            check=True,
            timeout=300,
        )
    if before != inputs():
        raise ValueError("96 MHz machine sources changed during compilation")
    write_json(
        RECEIPT,
        {"inputs": before, "binary": digest(BINARY), "idle_falling_edge": fast_falling_edge},
    )
    if not current():
        raise ValueError("96 MHz machine build is unverified")
    print("Built verified 96 MHz machine", flush=True)


if __name__ == "__main__":
    main()
