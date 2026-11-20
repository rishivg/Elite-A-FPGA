"""Check every completed ADC result against a pinned, independent C emulator."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import urllib.request

from tools.validation.common import ROOT, digest, source_root, write_json
from tools.validation.setup import verify

from . import adc_bus, build
from .probe import device_rtl

COMMIT = "2ff70a06e83ca5cfb09c0cdff2f23e5f4113b633"
PIN = {
    "adc.c": "b3e9bd1a6a9cff3e531d4d319b74ec6693e1b637f584cdf150fb90b4d4199fa2",
    "adc.h": "f056da23f28b18f92f405297b305376c5e51aced82244577da6044348a69ef2d",
    "timing.h": "4ee6a9abd59e47a67d77d48485bee307ae9158432fc33d126e8fee3378dc3aa9",
    "via.h": "1579f24bae79c18dc511782cef207c9a8fcd39a7fa99f79b24dcd981a4970b45",
    "util.h": "84af7d677fedfe5d7e2642a193f041a57e233e4a95bd846e63e53f6e758bb19c",
    "log.h": "28afd32fb4603aeb735a678c72030f45c04144fdb2694c0120810a3398d297ea",
    "LICENSE": "384608dfc681207f911ed051f8df01d95c5484675750d7280ed219a740ecfa6c",
    "COPYING": "3f6c9b4f7498497f0031ab581975015653c903e18edcb6b8fb86fd0fcb46d6df",
}
DIRECTORY = build.BBC / "adc-results"
RECEIPT = DIRECTORY / "receipt.json"
CORPUS = DIRECTORY / "expected.bin"
BINARY = DIRECTORY / "native/VBbcAdcValues"
RESULT_PROFILE = "bbc-result-bits"
CASES = 1048576


def files():
    return [
        ROOT / "tools/bbc/adc_results.py",
        ROOT / "tools/bbc/probe.py",
        ROOT / "tools/bbc/reference_adc_results.mjs",
        ROOT / "tools/bbc/reference_adc_values.mjs",
        ROOT / "sim/native/bbc_adc_values.v",
        ROOT / "sim/native/bbc_adc_values.cpp",
        ROOT / "sim/native/bbc_adc_oracle_support.c",
        *[DIRECTORY / "sources" / name for name in PIN],
        *adc_bus.files(),
        adc_bus.RECEIPT,
        adc_bus.BINARY,
        adc_bus.DIRECTORY / "devices.v",
    ]


def inputs():
    if not build.generation_current():
        raise ValueError("ADC RTL generation is stale; run make bbc-gen")
    for name, expected in PIN.items():
        if digest(DIRECTORY / "sources" / name) != expected:
            raise ValueError("Independent ADC source checksum mismatch: " + name)
    return {
        "sources": build.hashes(files()),
        "generated_adc": device_rtl(["Adc7002"]),
        "beebjit_commit": COMMIT,
        "tools": {
            name: subprocess.check_output([name, "--version"], text=True).splitlines()[0]
            for name in ["cc", "c++", "verilator", "node"]
        },
        "recipe": "Separate C -O3 without LTO; savable Verilator -O3 C++17 LTO; public externally-clocked ADC",
    }


def current():
    try:
        receipt = json.loads(RECEIPT.read_text())
        return (
            adc_bus.current()
            and receipt.get("passed") is True
            and receipt.get("inputs") == inputs()
            and receipt.get("rtl", {}).get("cases") == CASES
            and receipt["rtl"]["differences"] == 0
            and receipt["reference"]["profiles"][RESULT_PROFILE]
            == {"cases": CASES, "differences": 0}
            and receipt["reference"]["profiles"]["original"]["differences"] > 0
            and CORPUS.stat().st_size == CASES * 4
            and receipt.get("outputs") == build.hashes([BINARY, CORPUS])
        )
    except (OSError, ValueError, KeyError, TypeError):
        return False


def prepare():
    verify()
    adc_bus.prepare()
    directory = DIRECTORY / "sources"
    directory.mkdir(parents=True, exist_ok=True)
    for name in PIN:
        destination = directory / name
        if not destination.exists():
            url = f"https://raw.githubusercontent.com/scarybeasts/beebjit/{COMMIT}/{name}"
            with urllib.request.urlopen(url, timeout=30) as response:
                data = response.read()
            if hashlib.sha256(data).hexdigest() != PIN[name]:
                raise ValueError("Downloaded ADC source checksum mismatch: " + name)
            destination.write_bytes(data)
    if current():
        return json.loads(RECEIPT.read_text())
    before = inputs()
    rtl = DIRECTORY / "devices.v"
    rtl.write_text(before["generated_adc"])
    native = BINARY.parent
    native.mkdir(exist_ok=True)
    objects = []
    with (DIRECTORY / "build.log").open("w") as log:
        for source in [directory / "adc.c", ROOT / "sim/native/bbc_adc_oracle_support.c"]:
            obj = native / (source.stem + ".o")
            subprocess.run(
                ["cc", "-O3", "-std=c11", "-I" + str(directory), "-c", str(source), "-o", str(obj)],
                check=True,
                stdout=log,
                stderr=subprocess.STDOUT,
                timeout=30,
            )
            objects.append(str(obj))
        subprocess.run(
            [
                "verilator",
                "--cc",
                "--exe",
                "--build",
                "--savable",
                "-j",
                os.environ.get("VERILATOR_JOBS", "4"),
                "-Wno-fatal",
                "--top-module",
                "BbcAdcValues",
                "--Mdir",
                str(native),
                "-CFLAGS",
                "-O3 -std=c++17 -flto -Werror=return-type -I" + str(directory),
                "-LDFLAGS",
                "-flto " + " ".join(objects),
                "-MAKEFLAGS",
                "-B OPT_FAST=-O3",
                str(rtl),
                str(ROOT / "sim/native/bbc_adc_values.v"),
                str(ROOT / "sim/native/bbc_adc_values.cpp"),
            ],
            check=True,
            stdout=log,
            stderr=subprocess.STDOUT,
            timeout=120,
        )
    result = json.loads(subprocess.check_output([str(BINARY), str(CORPUS)], text=True, timeout=30))
    reference = json.loads(
        subprocess.check_output(
            [
                "node",
                str(ROOT / "tools/bbc/reference_adc_values.mjs"),
                str(source_root("jsbeeb")),
                str(CORPUS),
            ],
            text=True,
            timeout=30,
        )
    )
    if (
        before != inputs()
        or result["cases"] != CASES
        or result["differences"]
        or not reference["passed"]
    ):
        raise ValueError("ADC results failed or sources changed during execution")
    receipt = {
        "passed": True,
        "inputs": before,
        "rtl": result,
        "reference": reference,
        "outputs": build.hashes([BINARY, CORPUS]),
        "scope": "Completed result/status/CB1 only; busy reads, read acknowledgement, analogue accuracy and physical timing remain unproved",
    }
    write_json(RECEIPT, receipt)
    if not current():
        raise ValueError("Incomplete ADC result receipt")
    return receipt


if __name__ == "__main__":
    result = prepare()
    print(
        json.dumps(
            {
                "passed": result["passed"],
                "rtl": result["rtl"],
                "reference": result["reference"],
                "receipt": str(RECEIPT),
            },
            indent=2,
        )
    )
