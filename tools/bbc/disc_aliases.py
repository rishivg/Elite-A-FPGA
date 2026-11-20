"""Compare every 8271 register-space MMIO mirror through public host commands."""

import json
import re
import subprocess
from pathlib import Path

from tools.validation.common import ROOT, source_root, write_json

from . import build, disc_restart, probe


def qualify():
    directory = build.BBC / "disc-aliases"
    driver = ROOT / "sim/native/bbc_disc_test.cpp"
    reference = ROOT / "tools/bbc/reference_disc_aliases.mjs"
    sources = [
        Path(__file__),
        Path(disc_restart.__file__),
        driver,
        reference,
        ROOT / "tools/bbc/reference_drive.mjs",
    ]
    inputs = probe.compile_probe(directory, "Intel8271", sources, ["Intel8271"])
    before = build.hashes([*sources, build.BBC / "disc.ssd"])
    fixture = directory / "original.tsv"
    subprocess.run(
        [
            "node",
            str(reference),
            str(source_root("jsbeeb")),
            str(build.BBC / "disc.ssd"),
            str(fixture),
        ],
        cwd=ROOT,
        check=True,
        timeout=30,
    )
    metadata = json.loads(Path(str(fixture) + ".json").read_text())
    if metadata != {
        "cases": 128,
        "rows": 2432,
        "own_cold": True,
        "register_addresses": 32,
        "values": [0, 0x20, 0x55, 0x80],
        "aliases_per_register": 4,
    }:
        raise ValueError("Incomplete register alias fixture")
    writes = directory / "writes.tsv"
    result = subprocess.run(
        [str(directory / "VIntel8271"), str(fixture), str(build.BBC / "disc.ssd"), str(writes)],
        cwd=build.BBC,
        capture_output=True,
        text=True,
        timeout=30,
    )
    log = directory / "result.log"
    log.write_text(result.stdout + result.stderr)
    broken, count = re.subn(
        r"(?m)^  assign specialMmio = parameters_0\[5\];$",
        "  assign specialMmio = 1'b0;",
        (directory / "devices.v").read_text(),
    )
    if count != 1:
        raise ValueError("Missing actual MMIO selection negative-control target")
    fault_directory = directory / "mmio-as-ram"
    fault = disc_restart.compile_fault(
        fault_directory, broken, driver, "MMIO register selection disabled only"
    )
    fault_writes = fault_directory / "writes.tsv"
    negative = subprocess.run(
        [str(fault), str(fixture), str(build.BBC / "disc.ssd"), str(fault_writes)],
        cwd=build.BBC,
        capture_output=True,
        text=True,
        timeout=30,
    )
    fault_log = fault_directory / "result.log"
    fault_log.write_text(negative.stdout + negative.stderr)
    detected = (
        negative.returncode == 1
        and re.fullmatch(r"2432 original bus observations; [1-9]\d* differences\n", negative.stdout)
        is not None
    )
    stable = (
        before == build.hashes([*sources, build.BBC / "disc.ssd"]) and build.generation_current()
    )
    receipt = {
        "passed": stable
        and detected
        and result.returncode == 0
        and not result.stderr
        and result.stdout == "2432 original bus observations; 0 differences\n",
        "inputs": inputs,
        "sources": before,
        "inputs_unchanged": stable,
        **metadata,
        "artifacts": build.hashes(
            [
                fixture,
                Path(str(fixture) + ".json"),
                writes,
                log,
                fault_log,
                fault_writes,
                fault_directory / "devices.v",
                fault_directory / "build.json",
            ]
        ),
        "negative_control": {"name": "mmio-as-ram", "detected": detected},
        "scope": "Actual generated 8271 and original own cold state, standard declared SSD. Public WRITE/READ SPECIAL commands across all 32 MMIO aliases and four data values; every 32 MHz physical edge and accepted status/result/NMI observation. Commands and their own drive-selection side effects execute independently.",
        "limits": "Finite idle register histories; dynamic internal registers and raw writing/formatting are not proven.",
        "universal_game_equivalence": False,
    }
    write_json(directory / "result.json", receipt)
    print(f"FDC register aliases: {receipt['passed']}; {directory}/result.json", flush=True)
    if not receipt["passed"]:
        raise ValueError("FDC register aliases differ; complete histories retained")
    return receipt


if __name__ == "__main__":
    qualify()
