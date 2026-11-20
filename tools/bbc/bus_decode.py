"""Compare every Model B address with unchanged original device dispatch APIs."""

import json
import re
import subprocess
from pathlib import Path

from tools.validation.common import ROOT, digest, source_root, write_json
from tools.validation.setup import verify

from . import build

DIRECTORY = build.BBC / "bus-devices"


def elaborate():
    """Generate the standalone production classes without altering parent wiring."""
    DIRECTORY.mkdir(exist_ok=True)
    dependencies = [
        *ROOT.glob("rtl/**/*.scala"),
        ROOT / "build.sbt",
        ROOT / "project/build.properties",
    ]
    before = build.hashes(dependencies)
    outputs = [DIRECTORY / "BbcAddressDecode.v", DIRECTORY / "BbcSerial.v"]
    saved = build.load_receipt("bus-devices/elaboration.json")
    if (
        saved.get("inputs") != before
        or saved.get("outputs") != build.hashes(outputs)
        or len(build.hashes(outputs)) != 2
    ):
        with (DIRECTORY / "elaboration.log").open("w") as log:
            subprocess.run(
                ["sbt", "runMain leet.bbc.GenerateBbcBusDevices"],
                cwd=ROOT,
                stdout=log,
                stderr=subprocess.STDOUT,
                check=True,
                timeout=180,
            )
        if before != build.hashes(dependencies):
            raise ValueError("Bus device sources changed during elaboration")
        write_json(
            DIRECTORY / "elaboration.json", {"inputs": before, "outputs": build.hashes(outputs)}
        )
    return before


def compile_model(rtl, directory, *, top="BbcAddressDecode", driver=None):
    directory.mkdir(exist_ok=True)
    driver = driver or ROOT / "sim/native/bbc_address_decode_test.cpp"
    binary = directory / ("V" + top)
    inputs = {
        "sources": build.hashes([rtl, driver]),
        "verilator": subprocess.check_output(["verilator", "--version"], text=True).strip(),
        "compiler": subprocess.check_output(["c++", "--version"], text=True).splitlines()[0],
        "recipe": top + "; -O3 -std=c++17; OPT_FAST=-O3; independent fixture",
    }
    receipt = directory / "build.json"
    saved = json.loads(receipt.read_text()) if receipt.exists() else {}
    if (
        saved.get("inputs") != inputs
        or not binary.exists()
        or saved.get("binary") != digest(binary)
    ):
        with (directory / "build.log").open("w") as log:
            subprocess.run(
                [
                    "verilator",
                    "--cc",
                    "--exe",
                    "--build",
                    "-j",
                    "1",
                    "-Wno-fatal",
                    "--top-module",
                    top,
                    "--Mdir",
                    str(directory),
                    "-CFLAGS",
                    "-O3 -std=c++17 -Werror=return-type",
                    "-MAKEFLAGS",
                    "OPT_FAST=-O3",
                    str(rtl),
                    str(driver),
                ],
                cwd=ROOT,
                stdout=log,
                stderr=subprocess.STDOUT,
                check=True,
                timeout=120,
            )
        if inputs["sources"] != build.hashes([rtl, driver]):
            raise ValueError("Decoder sources changed during compilation")
        write_json(receipt, {"inputs": inputs, "binary": digest(binary)})
    return binary


def qualify():
    verify()
    generation = elaborate()
    directory = build.BBC / "bus-decode"
    directory.mkdir(exist_ok=True)
    helper = ROOT / "tools/bbc/reference_bus_decode.mjs"
    rtl = DIRECTORY / "BbcAddressDecode.v"
    guarded = [
        Path(__file__),
        helper,
        rtl,
        ROOT / "sim/native/bbc_address_decode_test.cpp",
        DIRECTORY / "elaboration.json",
        *sorted((source_root("jsbeeb") / "src").rglob("*.js")),
    ]
    before = build.hashes(guarded)
    fixture = directory / "original.tsv"
    subprocess.run(
        ["node", str(helper), str(source_root("jsbeeb")), str(fixture)], check=True, timeout=60
    )
    fixture_hash = digest(fixture)

    def execute(model, name):
        binary = compile_model(model, directory / name)
        start = digest(binary)
        result = subprocess.run(
            [str(binary), str(fixture)], capture_output=True, text=True, timeout=30
        )
        log = directory / (name + ".log")
        log.write_text(result.stdout + result.stderr)
        if start != digest(binary):
            raise ValueError("Decoder binary changed during comparison")
        return result

    positive = execute(rtl, "actual")
    negatives = []
    for name, target, wrong in (
        (
            "missing-fdc-aliases",
            "assign io_fdc = (io_address[15 : 5] == 11'h7f4);",
            "assign io_fdc = (io_address[15 : 3] == 13'h1fd0);",
        ),
        (
            "missing-ula-aliases",
            "assign io_ula = (io_address[15 : 4] == 12'hfe2);",
            "assign io_ula = (io_address[15 : 3] == 13'h1fc4);",
        ),
        (
            "missing-rom-aliases",
            "assign io_romSelect = ((io_address[15 : 3] == 13'h1fc6) || (io_address[15 : 2] == 14'h3f8f));",
            "assign io_romSelect = (io_address[15 : 2] == 14'h3f8c);",
        ),
        (
            "wrong-unimplemented-read",
            "io_unimplemented = io_address[15 : 8];",
            "io_unimplemented = 8'hff;",
        ),
    ):
        text = rtl.read_text()
        if text.count(target) != 1:
            raise ValueError("Missing actual decoder fault target: " + name)
        model = directory / (name + ".v")
        model.write_text(text.replace(target, wrong))
        result = execute(model, name)
        match = re.fullmatch(
            r"Model B decode: 65536 addresses; ([1-9]\d*) differences\n", result.stdout
        )
        negatives.append(
            {
                "name": name,
                "detected": result.returncode == 1 and match is not None,
                "differences": int(match[1]) if match else None,
            }
        )
    verify()
    stable = (
        before == build.hashes(guarded)
        and fixture_hash == digest(fixture)
        and generation == elaborate()
    )
    report = {
        "passed": stable
        and positive.returncode == 0
        and positive.stdout == "Model B decode: 65536 addresses; 0 differences\n"
        and all(n["detected"] for n in negatives),
        "production_bound": False,
        "addresses": 65536,
        "inputs": before,
        "generation": generation,
        "inputs_unchanged": stable,
        "fixture_sha256": fixture_hash,
        "negative_controls": negatives,
        "artifacts": build.hashes(
            list(directory.glob("*.v"))
            + list(directory.glob("*.log"))
            + [fixture]
            + list(directory.glob("*/build.json"))
            + list(directory.glob("*/VBbcAddressDecode"))
        ),
        "scope": "Complete 16-bit address domain for the standalone BbcAddressDecode class: read/write child routing and mirrored registers, all 1 MHz wait-state classifications and unimplemented-register values. Unchanged original Model B dispatch APIs execute independently; child callbacks identify routing and do not model register behavior. Four deliberate historical decoder faults must be detected.",
        "limits": "Parent wiring is checked separately by bus_binding_proof. This does not establish registered bus-data latency, CPU, child register behavior, serial timing or whole-game equivalence. Fixture has no Tube, Econet, music, teletext adaptor or other optional expansion.",
    }
    write_json(directory / "result.json", report)
    print(f"Model B decoder: {report['passed']}; standalone component", flush=True)
    if not report["passed"]:
        raise ValueError("Complete decoder comparison failed; see retained logs")
    return report


if __name__ == "__main__":
    qualify()
