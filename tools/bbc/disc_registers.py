"""Compare public internal-register reads after cold prefixes and transfers."""

import json
import re
import subprocess
from pathlib import Path

from tools.validation.common import ROOT, source_root, write_json

from . import build, disc_commands, disc_restart, probe

PREFIXES = {"cold": 105, "mode": 108, "data": 106, "ram": 110, "noparam": 114}
TRANSFERS = {"read": 803, "write": 803, "verify": 291, "id": 299}
MODE_ROWS = 111


def validate_cases(metadata, family):
    cases = metadata.get("cases", [])
    if metadata.get("own_cold_cases") is not True:
        raise ValueError("Original register histories must start independently cold")
    if family == "registers":
        expected = [
            (prefix, address, rows) for prefix, rows in PREFIXES.items() for address in range(32)
        ]
        actual = [(c.get("prefix"), c.get("address"), c.get("rows")) for c in cases]
        names = [f"{c.get('prefix')}-{c.get('address')}" for c in cases]
        if any(type(c.get("address")) is not int for c in cases):
            raise ValueError("Malformed original register addresses")
    elif family == "register_transfers":
        expected = list(TRANSFERS.items())
        actual = [(c.get("name"), c.get("rows")) for c in cases]
        names = [c.get("name") for c in cases]
        if any(
            not isinstance(c.get("registers"), list)
            or len(c["registers"]) != 32
            or any(
                not isinstance(r, list)
                or len(r) != 2
                or type(r[0]) is not int
                or type(r[1]) is not int
                or not 0 <= r[1] <= 255
                for r in c["registers"]
            )
            or [r[0] for r in c["registers"]] != list(range(32))
            for c in cases
        ):
            raise ValueError("Incomplete original post-transfer register sweep")
    elif family == "mode_registers":
        expected = [(mode, MODE_ROWS) for mode in range(256)]
        actual = [(c.get("mode"), c.get("rows")) for c in cases]
        names = [str(c.get("mode")) for c in cases]
        if any(
            type(c.get("mode")) is not int
            or any(
                type(c.get(key)) is not int or not 0 <= c[key] <= 255 for key in ("value", "second")
            )
            for c in cases
        ):
            raise ValueError("Malformed original shared-status reads")
    else:
        raise ValueError("Unknown original register family")
    if (
        actual != expected
        or any(type(c.get("rows")) is not int for c in cases)
        or any(
            c.get("fixture") != f"original-{family}.json.{name}.tsv"
            for c, name in zip(cases, names, strict=True)
        )
    ):
        raise ValueError("Incomplete original register histories")
    return [(name, c) for name, c in zip(names, cases, strict=True)]


def complete(result, rows):
    return (
        type(rows) is int
        and rows in {*PREFIXES.values(), *TRANSFERS.values(), MODE_ROWS}
        and result.returncode == 0
        and not result.stderr
        and result.stdout == f"{rows} original bus observations; 0 differences\n"
    )


def disconnected_header(text):
    text, count = re.subn(r"liveSpecial = headerPointer;", "liveSpecial = 8'hff;", text)
    if count != 1:
        raise ValueError("Missing actual header-pointer fault target")
    return text


def conflated_nmi(text):
    text, count = re.subn(
        r"(assign io_nmi = \(\(\()eventNmi( \|\| commandNmi\))",
        r"\g<1>eventInterrupt\2",
        text,
    )
    if count != 1:
        raise ValueError("Missing unique actual NMI-pin driver")
    return text


def qualify():
    directory = build.BBC / "disc-registers"
    driver = ROOT / "sim/native/bbc_disc_test.cpp"
    references = {
        family: ROOT / f"tools/bbc/reference_disc_{family}.mjs"
        for family in ("registers", "register_transfers", "mode_registers")
    }
    sources = [
        Path(__file__),
        Path(disc_commands.__file__),
        Path(disc_restart.__file__),
        driver,
        *references.values(),
        ROOT / "tools/bbc/reference_drive.mjs",
    ]
    inputs = probe.compile_probe(directory, "Intel8271", sources, ["Intel8271"])
    before = build.hashes([*sources, build.BBC / "disc.ssd"])
    results, artifacts = [], []
    fault_fixture = None
    for family, reference in references.items():
        original = directory / f"original-{family}.json"
        subprocess.run(
            [
                "node",
                str(reference),
                str(source_root("jsbeeb")),
                str(build.BBC / "disc.ssd"),
                str(original),
            ],
            cwd=ROOT,
            check=True,
            timeout=60,
        )
        cases = validate_cases(json.loads(original.read_text()), family)
        artifacts.append(original)
        for name, case in cases:
            fixture = directory / case["fixture"]
            disc_commands.validate_fixture(fixture, case["rows"], 0)
            writes = directory / f"{family}-{name}.writes.tsv"
            run = subprocess.run(
                [
                    str(directory / "VIntel8271"),
                    str(fixture),
                    str(build.BBC / "disc.ssd"),
                    str(writes),
                ],
                cwd=build.BBC,
                capture_output=True,
                text=True,
                timeout=300,
            )
            log = directory / f"{family}-{name}.log"
            log.write_text(run.stdout + run.stderr)
            payload = "write" if family == "register_transfers" and name == "write" else "read"
            results.append(
                {
                    "family": family,
                    "name": name,
                    "rows": case["rows"],
                    "passed": complete(run, case["rows"])
                    and disc_commands.expected_payload(writes, payload),
                }
            )
            artifacts.extend([fixture, writes, log])
            if family == "register_transfers" and name == "read":
                fault_fixture = fixture
        print(
            f"FDC {family}: {all(r['passed'] for r in results if r['family'] == family)}",
            flush=True,
        )
    negatives = [
        {"name": name, "attempted": False, "detected": False}
        for name in ("disconnected-header-pointer", "conflated-nmi-pin")
    ]
    if all(r["passed"] for r in results):
        for negative, mutate, fixture, rows in (
            (negatives[0], disconnected_header, fault_fixture, 803),
            (
                negatives[1],
                conflated_nmi,
                directory / "original-mode_registers.json.8.tsv",
                MODE_ROWS,
            ),
        ):
            fault_directory = directory / negative["name"]
            binary = disc_restart.compile_fault(
                fault_directory,
                mutate((directory / "devices.v").read_text()),
                driver,
                "actual " + negative["name"] + " fault only",
            )
            writes = fault_directory / "writes.tsv"
            run = subprocess.run(
                [str(binary), str(fixture), str(build.BBC / "disc.ssd"), str(writes)],
                cwd=build.BBC,
                capture_output=True,
                text=True,
                timeout=300,
            )
            log = fault_directory / "result.log"
            log.write_text(run.stdout + run.stderr)
            negative.update(
                attempted=True,
                detected=run.returncode == 1
                and re.fullmatch(
                    rf"{rows} original bus observations; [1-9]\d* differences\n", run.stdout
                )
                is not None,
            )
            artifacts.extend(
                [writes, log, fault_directory / "devices.v", fault_directory / "build.json"]
            )
    stable = (
        before == build.hashes([*sources, build.BBC / "disc.ssd"])
        and build.generation_current()
        and probe.device_rtl(["Intel8271"]) == (directory / "devices.v").read_text()
    )
    receipt = {
        "passed": stable
        and all(r["passed"] for r in results)
        and all(n["detected"] for n in negatives),
        "inputs": inputs,
        "sources": before,
        "inputs_unchanged": stable,
        "results": results,
        "rows": sum(r["rows"] for r in results),
        "negative_controls": negatives,
        "artifacts": build.hashes(artifacts),
        "scope": "420 own cold public original internal-register histories. Complete bank after cold/mode/data/SPECIFY/no-parameter prefixes and after READ/WRITE/VERIFY/READ ID, plus all 256 writable mode values. All physical 32 MHz edges execute; complete stored WRITE payload and deadlines checked. No original state enters DUT.",
        "limits": "Finite completed-command histories, not arbitrary internal-register writes, active header/timer interruption phases, formatting, raw flux or universal game equivalence.",
        "universal_game_equivalence": False,
    }
    write_json(directory / "result.json", receipt)
    if not receipt["passed"]:
        raise ValueError("FDC internal registers differ: " + str(directory))
    return receipt


if __name__ == "__main__":
    qualify()
