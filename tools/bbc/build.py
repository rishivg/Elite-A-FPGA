"""Content-checked BBC elaboration/native builds; no timestamp-only reuse."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path

from tools.validation.common import ROOT, digest, write_json

from . import cpu_events

BBC = ROOT / "build/bbc"


def hashes(paths):
    return {str(p.relative_to(ROOT)): digest(p) for p in sorted(paths) if p.is_file()}


def generation_inputs():
    return hashes(
        [
            *ROOT.glob("rtl/bbc/*.scala"),
            ROOT / "rtl/audio/Psg.scala",
            *ROOT.glob("rtl/video/*.scala"),
            ROOT / "tools/bbc/reference_teletext_font.mjs",
            BBC / "teletext-font.hex",
            ROOT / "rtl/bbc/BbcCpu.v",
            *ROOT.glob("third_party/6502/*.v"),
            BBC / "ArletCpuEnabled.v",
            BBC / "rom.hex",
            ROOT / "build.sbt",
            ROOT / "project/build.properties",
            ROOT / "tools/bbc/prepare.py",
            ROOT / "tools/bbc/cpu_bus.py",
            Path(__file__),
            *cpu_events.files(),
        ]
    )


def generated():
    return hashes([BBC / "BbcMachine.v", BBC / "BbcSystemTop.v", *BBC.glob("Bbc*.v_*.bin")])


def load_receipt(name):
    try:
        return json.loads((BBC / name).read_text())
    except (OSError, ValueError):
        return {}


def generation_current():
    try:
        cpu_events.audit()
    except (OSError, ValueError):
        return False
    receipt = load_receipt("elaboration.json")
    return (
        bool(receipt.get("outputs"))
        and receipt.get("inputs") == generation_inputs()
        and receipt["outputs"] == generated()
    )


def native_cwd(clock_multiplier=1):
    if clock_multiplier not in {1, 3}:
        raise ValueError("Unsupported BBC master-clock multiplier")
    return BBC if clock_multiplier == 1 else BBC / "full-video"


def native_binary(clock_multiplier=1):
    if clock_multiplier == 1:
        return BBC / "native/VBbcMachine"
    if clock_multiplier == 3:
        return BBC / "full-video/native/VBbcMachine96"
    raise ValueError("Unsupported BBC master-clock multiplier")


def native_inputs(clock_multiplier=1):
    if clock_multiplier == 3:
        from . import system96

        return system96.inputs()
    if clock_multiplier != 1:
        raise ValueError("Unsupported BBC master-clock multiplier")
    return {
        "generation": generation_inputs(),
        "generated": generated(),
        "runner": digest(ROOT / "sim/native/bbc.cpp"),
        "audio_capture": digest(ROOT / "sim/native/bbc_audio.h"),
        "raw_medium": hashes(
            [ROOT / "sim/native/bbc_fm_media.h", ROOT / "sim/native/bbc_disc_medium.h"]
        ),
        "field_capture": digest(ROOT / "sim/native/bbc_frame_hash.h"),
        "crypto": subprocess.check_output(
            ["pkg-config", "--modversion", "libcrypto"], text=True
        ).strip(),
        "verilator": subprocess.check_output(["verilator", "--version"], text=True).strip(),
        "compiler": subprocess.check_output(["c++", "--version"], text=True).splitlines()[0],
    }


def native_current(clock_multiplier=1):
    if clock_multiplier == 3:
        from . import system96

        return system96.current()
    if clock_multiplier != 1:
        raise ValueError("Unsupported BBC master-clock multiplier")
    receipt = load_receipt("native-build.json")
    binary = BBC / "native/VBbcMachine"
    valid = (
        generation_current()
        and binary.is_file()
        and receipt.get("inputs") == native_inputs()
        and receipt.get("binary") == digest(binary)
    )
    if valid and receipt.get("optimization"):
        from .profile import current

        valid = current(receipt["optimization"])
    return valid


def main():
    cpu_events.prepare()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--generate-only", action="store_true")
    args = parser.parse_args()
    if not generation_current():
        inputs = generation_inputs()
        subprocess.run(
            [
                os.environ.get("SBT", "sbt"),
                "runMain leet.bbc.Generate",
                "runMain leet.bbc.GenerateSystem",
            ],
            cwd=ROOT,
            check=True,
        )
        for name in ("BbcMachine.v", "BbcSystemTop.v"):
            cpu_events.inline_file(BBC / name)
        if inputs != generation_inputs():
            raise ValueError("RTL source changed during elaboration")
        write_json(BBC / "elaboration.json", {"inputs": inputs, "outputs": generated()})
    else:
        print("Verified unchanged BBC elaboration; reusing generated RTL", flush=True)
    if args.generate_only:
        return
    if not native_current():
        inputs = native_inputs()
        subprocess.run(
            [
                "verilator",
                "--cc",
                "--exe",
                "--build",
                "-j",
                os.environ.get("VERILATOR_JOBS", "4"),
                "--savable",
                "-Wno-fatal",
                "--top-module",
                "BbcMachine",
                "-CFLAGS",
                "-O3 -std=c++17 -flto",
                "-LDFLAGS",
                "-flto -lcrypto",
                "-MAKEFLAGS",
                "-B OPT_FAST=-O3",
                "--Mdir",
                "build/bbc/native",
                "build/bbc/BbcMachine.v",
                "rtl/bbc/BbcCpu.v",
                "build/bbc/ArletCpuEnabled.v",
                "third_party/6502/ALU.v",
                str(ROOT / "sim/native/bbc.cpp"),
            ],
            cwd=ROOT,
            check=True,
        )
        if inputs != native_inputs():
            raise ValueError("RTL or native source changed during compilation")
        write_json(
            BBC / "native-build.json",
            {"inputs": inputs, "binary": digest(BBC / "native/VBbcMachine")},
        )
    else:
        print("Verified unchanged BBC native build; reusing simulator", flush=True)


if __name__ == "__main__":
    main()
