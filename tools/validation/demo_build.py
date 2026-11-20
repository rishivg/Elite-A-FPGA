"""Build one content-verified native Soc for all frame-driven demo suites."""

from __future__ import annotations

import argparse
import json
import os
import subprocess

from .common import ROOT, digest, write_json

DIRECTORY = ROOT / "build/demo"
BINARY = DIRECTORY / "native/VSoc"


def inputs() -> dict:
    paths = [
        *ROOT.glob("rtl/**/*.scala"),
        *ROOT.glob("fw/*.c"),
        *ROOT.glob("fw/*.h"),
        ROOT / "fw/crt0.S",
        ROOT / "fw/link.ld",
        ROOT / "fw/Makefile",
        ROOT / "tools/bin2hex.py",
        ROOT / "build/fw/firmware.hex",
        ROOT / "third_party/picorv32.v",
        ROOT / "sim/native/soc_frames.cpp",
        ROOT / "build.sbt",
        ROOT / "project/build.properties",
        ROOT / "tools/validation/demo_build.py",
    ]
    return {
        "sources": {str(p.relative_to(ROOT)): digest(p) for p in sorted(paths)},
        "verilator": subprocess.check_output(["verilator", "--version"], text=True).strip(),
        "compiler": subprocess.check_output(["c++", "--version"], text=True).splitlines()[0],
    }


def outputs() -> dict:
    paths = [DIRECTORY / "Soc.v", *DIRECTORY.glob("Soc.v_*.bin"), BINARY]
    return {str(p.relative_to(DIRECTORY)): digest(p) for p in sorted(paths) if p.is_file()}


def current(expected_inputs: dict | None = None) -> bool:
    try:
        saved = json.loads((DIRECTORY / "native-build.json").read_text())
        return (
            BINARY.is_file()
            and (DIRECTORY / "Soc.v").is_file()
            and saved.get("inputs") == (inputs() if expected_inputs is None else expected_inputs)
            and saved.get("outputs") == outputs()
        )
    except (OSError, ValueError):
        return False


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()
    receipt = DIRECTORY / "native-build.json"
    before = inputs()
    if current(before):
        print("Verified unchanged demo simulator; reusing native build", flush=True)
        return
    if args.check_only:
        raise ValueError("Demo simulator missing or stale; run make demo-build before sbt")
    subprocess.run(
        [os.environ.get("SBT", "sbt"), "runMain leet.GenerateSocRegression"], cwd=ROOT, check=True
    )
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
            "Soc",
            "-CFLAGS",
            "-O3 -std=c++17 -flto",
            "-LDFLAGS",
            "-flto",
            "-MAKEFLAGS",
            "-B OPT_FAST=-O3",
            "--Mdir",
            "build/demo/native",
            "build/demo/Soc.v",
            "third_party/picorv32.v",
            str(ROOT / "sim/native/soc_frames.cpp"),
        ],
        cwd=ROOT,
        check=True,
    )
    if inputs() != before:
        raise ValueError("Demo sources changed during compilation")
    write_json(receipt, {"inputs": before, "outputs": outputs()})


if __name__ == "__main__":
    main()
