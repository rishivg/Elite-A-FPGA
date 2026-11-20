"""Prove the actual closed-fixture serial device against an elapsed-time contract."""

import json
import subprocess
from pathlib import Path

from tools.validation.common import ROOT, digest, write_json

from . import build, bus_decode, serial_idle_proof


def sealed(path):
    report = json.loads(path.read_text())
    if report.get("passed") is not True or report.get("inputs_unchanged") is not True:
        raise ValueError("Incomplete serial supporting evidence: " + str(path))
    for category in ("inputs", "artifacts"):
        hashes = report.get(category, {})
        if not hashes or any(digest(ROOT / name) != sha for name, sha in hashes.items()):
            raise ValueError("Changed serial supporting evidence: " + str(path))
    return report


def qualify(*, component_only=False):
    generation = bus_decode.elaborate()
    source = bus_decode.DIRECTORY / "BbcSerial.v"
    support = []
    if not component_only:
        support = [build.BBC / "bus-binding-proof/result.json"]
        support += [build.BBC / f"machine-clock-proof{m}/result.json" for m in (1, 3)]
        binding = sealed(support[0])
        if not (
            {parent.get("clock_multiplier") for parent in binding["clocks"]} == {1, 3}
            and len(binding["clocks"]) == 2
            and all(
                parent.get("passed") is True and parent.get("negative_control_detected") is True
                for parent in binding["clocks"]
            )
        ):
            raise ValueError("Incomplete actual parent serial binding")
        for path in support[1:]:
            if sealed(path).get("negative_control_detected") is not True:
                raise ValueError("Actual parent clock negative control was not detected")
    original_path = build.BBC / "serial-probe-fast/result.json"
    original = sealed(original_path)
    if not (
        original.get("counts", {}).get("cases") == 12928
        and original.get("counts", {}).get("observations") == 142720
        and len(original.get("clocks", [])) == 2
        and {clock.get("clock_multiplier") for clock in original["clocks"]} == {1, 3}
        and all(clock.get("passed") is True for clock in original["clocks"])
        and len(original.get("negative_controls", [])) == 2
        and all(control.get("detected") is True for control in original["negative_controls"])
    ):
        raise ValueError("Incomplete independent original serial semantics comparison")
    support.append(original_path)
    contract = ROOT / "sim/formal/BbcSerialContract.v"
    dependencies = [Path(__file__), Path(serial_idle_proof.__file__), source, contract, *support]
    before = build.hashes(dependencies)
    directory = build.BBC / (
        "serial-component-state-proof" if component_only else "serial-state-proof"
    )
    directory.mkdir(exist_ok=True)
    actual = source.read_text()
    model = directory / "miter.v"
    model.write_text(serial_idle_proof.observed_module(actual) + contract.read_text())

    def prove(path, label):
        command = (
            f"read_verilog -formal {path}; prep -top SerialContract -flatten; async2sync; opt_clean; "
            "sat -set-assumes -set-init-zero -tempinduct -seq 3 -maxsteps 8 "
            "-prove equivalent 1 -verify -timeout 90"
        )
        result = subprocess.run(
            ["yosys", "-Q", "-T", "-p", command], capture_output=True, text=True, timeout=120
        )
        (directory / (label + ".log")).write_text(result.stdout + result.stderr)
        return result

    positive = prove(model, "proof")
    targets = [
        ("wrong-completion", "(remaining == 19'h00001)", "(remaining == 19'h00002)"),
        ("wrong-serial-read", "8'hfe", "8'hfd"),
    ]
    negatives = []
    for name, correct, wrong in targets:
        if actual.count(correct) != 1:
            raise ValueError("Missing actual serial mutation target: " + name)
        path = directory / (name + ".v")
        path.write_text(
            serial_idle_proof.observed_module(actual.replace(correct, wrong)) + contract.read_text()
        )
        result = prove(path, name)
        negatives.append(
            {"name": name, "detected": result.returncode != 0 and "proof did fail" in result.stderr}
        )
    stable = generation == bus_decode.elaborate() and before == build.hashes(dependencies)
    report = {
        "passed": positive.returncode == 0 and stable and all(n["detected"] for n in negatives),
        "production_bound": not component_only,
        "inputs": before,
        "inputs_unchanged": stable,
        "negative_controls": negatives,
        "independent_semantics": original["counts"],
        "artifacts": build.hashes([*directory.glob("*.v"), *directory.glob("*.log")]),
        "scope": "Temporal induction of all 52 actual MC6850/serial ULA state bits and all digital outputs against an independent elapsed-time contract. Arbitrary accepted controls, data, addresses, ticks, framing/baud changes, restarts, master reset and deadline collisions after the declared reset. "
        + (
            "The run covers the standalone component; parent wiring and clocks need separate checks."
            if component_only
            else "Actual parent decoder, IRQ wiring, mutually exclusive bus commands and 2 MHz clock bindings are separately checked at both rates."
        ),
        "limits": "Local digital contract for the fixture with no tape or RS-423 peer. Does not establish receive behavior, physical signals, warm reset equivalence to the emulator, universal CPU or full machine/game equivalence. Independent original-device comparisons separately establish the declared register/deadline semantics.",
    }
    write_json(directory / "result.json", report)
    print(f"Actual serial temporal proof: {report['passed']}", flush=True)
    if not report["passed"]:
        raise ValueError("Serial temporal proof failed; logs and private mutations retained")
    return report


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--component-only",
        action="store_true",
        help="Check the standalone device without rebuilding or claiming parent bindings",
    )
    qualify(component_only=parser.parse_args().component_only)
