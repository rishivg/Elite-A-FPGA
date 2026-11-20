"""Exhaust every DRAM address wiring input against unchanged beebjit C code."""

import subprocess
from pathlib import Path

from tools.validation.common import ROOT, write_json

from . import build, crtc_registers
from .probe import compile_probe


def qualify():
    directory = build.BBC / "video-address"
    directory.mkdir(exist_ok=True)
    source = crtc_registers.independent_sources()
    driver = ROOT / "sim/native/bbc_video_address_oracle.c"
    support = ROOT / "sim/native/bbc_crtc_register_support.c"
    dependencies = [
        Path(__file__),
        ROOT / "tools/bbc/crtc_registers.py",
        driver,
        support,
        ROOT / "sim/native/bbc_video_address_test.cpp",
        *[source / name for name in crtc_registers.PIN],
    ]
    before = build.hashes(dependencies)
    binary = directory / "oracle"
    with (directory / "oracle-build.log").open("w") as log:
        subprocess.run(
            [
                "cc",
                "-O2",
                "-ffunction-sections",
                "-fdata-sections",
                "-DBEEBJIT_HEADLESS",
                "-I" + str(source),
                str(driver),
                str(support),
                *[str(source / name) for name in crtc_registers.SOURCES if name != "video.c"],
                "-Wl,--gc-sections",
                "-lm",
                "-lpthread",
                "-o",
                str(binary),
            ],
            stdout=log,
            stderr=subprocess.STDOUT,
            check=True,
            timeout=120,
        )
    corpus = directory / "addresses.bin"
    subprocess.run([str(binary), str(corpus)], check=True, timeout=30)
    inputs = compile_probe(directory, "BbcVideoAddress", dependencies, ["BbcVideoAddress"])
    artifacts = build.hashes(
        [binary, directory / "VBbcVideoAddress", corpus, directory / "devices.v"]
    )
    result = subprocess.run(
        [str(directory / "VBbcVideoAddress"), str(corpus)],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    print(result.stdout.strip(), flush=True)
    if (
        corpus.stat().st_size != 8388608
        or before != build.hashes(dependencies)
        or artifacts
        != build.hashes([binary, directory / "VBbcVideoAddress", corpus, directory / "devices.v"])
        or not build.generation_current()
    ):
        raise ValueError("Video address source or observations changed")
    receipt = {
        "passed": True,
        "addresses": 4194304,
        "byte_planes": 8388608,
        "inputs": inputs,
        "original_sources": before,
        "artifacts": artifacts,
        "original_commit": crtc_registers.COMMIT,
        "scope": "Actual unchanged C video_read_data_byte invoked from an unchanged included video.c translation unit, separately compiled without LTO. Public constructor/reset/ULA APIs establish the device; two ordinary memory planes reveal its complete 15-bit read address. Every MA, low raster, scroll, fast/slow, teletext/bitmap and refresh phase input is compared against the production combinational mapper. No C model state injection.",
        "limits": "Proves address translation at declared pins, not physical DRAM setup/hold, CRTC output phase or universal game equivalence.",
    }
    write_json(directory / "result.json", receipt)
    return receipt


if __name__ == "__main__":
    qualify()
