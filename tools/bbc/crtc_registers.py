"""BBC Hitachi CRTC register reads against an unmodified independent emulator."""

from __future__ import annotations

import hashlib
import subprocess
import time
import urllib.request
from pathlib import Path

from tools.bbc import build
from tools.bbc.probe import compile_probe, device_rtl
from tools.validation.common import digest, write_json
from tools.validation.setup import verify

COMMIT = "2ff70a06e83ca5cfb09c0cdff2f23e5f4113b633"
PIN = {
    "COPYING": "3f6c9b4f7498497f0031ab581975015653c903e18edcb6b8fb86fd0fcb46d6df",
    "LICENSE": "384608dfc681207f911ed051f8df01d95c5484675750d7280ed219a740ecfa6c",
    "bbc.h": "5a0a64d139259b426bf4dd9bce9208f2054e4abcef4b587828d7fecb1d1f0cd2",
    "bbc_options.h": "7aeb252d313f7d3b8cf2f562c3e73b7474d53bc657b9bfeedc875bffcddf2acf",
    "cmos.h": "8cc90214d8e4a2eefc6765585101664823d5bfccf63fdcd2fa253e71aa8b08f4",
    "keyboard.h": "e972472bb68d0e15a06be482a4398666cfe6426778df5869baeef4dd8e38ccf0",
    "log.c": "eb85c2997f4b1c85fcf177ee787f535de7447f170d5c9e12b55bf94e3b2e7abd",
    "log.h": "28afd32fb4603aeb735a678c72030f45c04144fdb2694c0120810a3398d297ea",
    "render.c": "22528be59e904782f1e90fa6cd0430ce8fc1322123e99e949668742b23346cf2",
    "render.h": "1814af0dbdb89e3d0189b742b84367ab367f2b3354a0f40bd9030887a519c843",
    "sound.h": "0a6614cdbedbd944fd23ac18361adffefeda2c34314491fd68bfb58d38b8bccc",
    "state_6502.h": "5d10e63cba6f917f783e84491a0d1a7e0b028084f588378ed33ff621cb48697f",
    "teletext.c": "0fb6c3289f3b369f141a96ef3bb36836b7965b2253f185bfd3996e8b26123334",
    "teletext.h": "ce8082499ce65c28e58e50dcea8689f2df6c77ad12e834d6058f55cc1c1cc323",
    "teletext_glyphs.h": "181ffbaceb4144265cf5f37f19481c7078265e4925253c6504c61c7539a9d154",
    "test-timing.c": "c88579d7162d202daa0c4c673a7dd8f3031b4216d8f80077a27475dd7910a7f4",
    "test-video.c": "6713a8f9df6a92bbc6f67c86847f41ed47a8b0430d6e7fbab9179ee18d967d5b",
    "test.h": "2aa7745bf65a536a10ec50a7923f460c8c389e447bde3775a6d2ba3cd6e6abc7",
    "timing.c": "9bcdfce4db44217be6a77adacf4b1268e108d3f9fc54490ea70f27c819fa4502",
    "timing.h": "4ee6a9abd59e47a67d77d48485bee307ae9158432fc33d126e8fee3378dc3aa9",
    "util.c": "c99674bb1c1694a1ba71fb558b99df6c8be9c21beb7358764af489d9439310e4",
    "util.h": "84af7d677fedfe5d7e2642a193f041a57e233e4a95bd846e63e53f6e758bb19c",
    "util_container.h": "a76f87129b3847e6ad0e4a121c2a3278c47694cf333af46c237b108413779649",
    "util_string.c": "5717e2dccf377b01d691a9d9c0819955af294a12e5da527a2d1b3b28f7789626",
    "util_string.h": "fedc28d18533e44335093a1f837d458d5435d603df02a34a4c9652dda32f96f9",
    "via.c": "555c1228d376ad21706a7ecad0e9266a9c2a082bbaab5236aaa6d6a38bdcec23",
    "via.h": "1579f24bae79c18dc511782cef207c9a8fcd39a7fa99f79b24dcd981a4970b45",
    "video.c": "f14b8b6ee654ef95c4ec0b454e260bbe12bdd7527e15301fbc0062e07c96b382",
    "video.h": "e45438acd2783116389ee70c2b5d3340ddfcc7a93cef1028be101dcf61f87837",
}
SOURCES = [
    "video.c",
    "render.c",
    "teletext.c",
    "timing.c",
    "util.c",
    "util_string.c",
    "log.c",
    "via.c",
]
DIRECTORY = build.BBC / "crtc-registers"
CODE_ROOT = Path(__file__).resolve().parents[2]
NATIVE = CODE_ROOT / "sim/native"
CORPUS = DIRECTORY / "expected.bin"


def independent_sources():
    directory = DIRECTORY / "sources"
    directory.mkdir(parents=True, exist_ok=True)
    for name, expected in PIN.items():
        destination = directory / name
        if not destination.exists():
            url = f"https://raw.githubusercontent.com/scarybeasts/beebjit/{COMMIT}/{name}"
            with urllib.request.urlopen(url, timeout=30) as response:
                data = response.read()
            if hashlib.sha256(data).hexdigest() != expected:
                raise ValueError("Independent CRTC source checksum mismatch: " + name)
            destination.write_bytes(data)
        if digest(destination) != expected:
            raise ValueError("Independent CRTC source checksum mismatch: " + name)
    return directory


def qualify():
    verify()
    sources = independent_sources()
    oracle_source = NATIVE / "bbc_crtc_register_oracle.c"
    support = NATIVE / "bbc_crtc_register_support.c"
    dependencies = [
        Path(__file__),
        oracle_source,
        support,
        NATIVE / "bbc_crtc_register_test.cpp",
        *[sources / name for name in PIN],
    ]
    before = build.hashes(dependencies)
    # The independent C model is separately compiled, without LTO. Public
    # constructors/reset/register APIs execute every expected bus read.
    oracle = DIRECTORY / "oracle"
    with (DIRECTORY / "oracle-build.log").open("w") as log:
        subprocess.run(
            [
                "cc",
                "-O2",
                "-ffunction-sections",
                "-fdata-sections",
                "-DBEEBJIT_HEADLESS",
                "-I" + str(sources),
                str(oracle_source),
                str(support),
                *[str(sources / name) for name in SOURCES],
                "-Wl,--gc-sections",
                "-lm",
                "-lpthread",
                "-o",
                str(oracle),
            ],
            stdout=log,
            stderr=subprocess.STDOUT,
            check=True,
            timeout=120,
        )
    with (DIRECTORY / "oracle.log").open("w") as log:
        subprocess.run(
            [str(oracle), str(CORPUS)], stdout=log, stderr=subprocess.STDOUT, check=True, timeout=30
        )
    if CORPUS.stat().st_size != 16384:
        raise ValueError("Truncated independent CRTC register corpus")
    inputs = compile_probe(
        DIRECTORY, "BbcVideo", dependencies, ["BbcVideo", "Crtc6845", "BbcVideoAddress"]
    )
    binary = DIRECTORY / "VBbcVideo"
    executed = build.hashes([oracle, binary, CORPUS])
    started = time.monotonic()
    result = subprocess.run(
        [str(DIRECTORY / "VBbcVideo"), str(CORPUS), str(DIRECTORY / "differences.tsv")],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if (
        before != build.hashes(dependencies)
        or executed != build.hashes([oracle, binary, CORPUS])
        or not build.generation_current()
        or device_rtl(["BbcVideo", "Crtc6845", "BbcVideoAddress"])
        != (DIRECTORY / "devices.v").read_text()
    ):
        raise ValueError("CRTC probe/reference sources changed during execution")
    expected = "CRTC registers: 8192 writes; 16384 reads; 0 differences"
    print(result.stdout.strip())
    receipt = {
        "passed": result.returncode == 0 and result.stdout.strip() == expected,
        "register_data_writes": 8192,
        "bus_reads": 16384,
        "inputs": inputs,
        "executed": executed,
        "independent_source_commit": COMMIT,
        "oracle_binary": digest(oracle),
        "corpus": digest(CORPUS),
        "elapsed_seconds": time.monotonic() - started,
        "coverage": "All 32 index values, all 256 data bytes, both CRTC ports; writable masks, readable start/cursor addresses, inactive read-only light-pen registers and unmapped indices",
        "limits": "No light-pen capture, physical bus acceptance or display timing is inferred from this register-only fixture",
    }
    write_json(DIRECTORY / "result.json", receipt)
    result.check_returncode()
    if not receipt["passed"]:
        raise ValueError("Incomplete CRTC register qualification")
    return receipt


if __name__ == "__main__":
    qualify()
