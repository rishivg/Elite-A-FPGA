"""Prove all ADC digital histories against the declared nominal timing contract."""

import json
import re
import subprocess
from pathlib import Path

from tools.validation.common import ROOT, digest, write_json
from tools.validation.setup import verify

from . import adc_results, build, video96
from .probe import device_rtl


def observed_module(text):
    """Expose state without changing any functional declaration or statement."""
    if not re.fullmatch(r"module\s+Adc7002\b[\s\S]*endmodule\s*", text):
        raise ValueError("Expected the actual ADC module only")
    declarations = {
        "control": r"reg\s+\[3:0\]\s+control\s*;",
        "resultControl": r"reg\s+\[3:0\]\s+resultControl\s*;",
        "conversion": r"reg\s+\[13:0\]\s+conversion\s*;",
        "busy": r"reg\s+busy\s*;",
        "completed": r"reg\s+completed\s*;",
        "sample": r"reg\s+\[15:0\]\s+sample\s*;",
    }
    if any(len(re.findall(pattern, text)) != 1 for pattern in declarations.values()):
        raise ValueError("Changed ADC state declarations; review the proof binding")
    header = text.index(");")
    ports = (
        ", output wire [3:0] proofControl, proofResultControl,\n"
        "output wire [13:0] proofRemaining, output wire proofBusy, proofCompleted,\n"
        "output wire [15:0] proofSample\n"
    )
    assignments = (
        "assign proofControl = control;\nassign proofResultControl = resultControl;\n"
        "assign proofRemaining = conversion;\nassign proofBusy = busy;\n"
        "assign proofCompleted = completed;\nassign proofSample = sample;\n"
    )
    return (
        text[:header]
        + ports
        + text[header : text.rindex("endmodule")]
        + assignments
        + "endmodule\n"
    )


def clock_evidence(path, multiplier):
    """A local timer proof requires a current, fault-checked actual parent clock."""
    report = json.loads(path.read_text())
    if not (
        report.get("passed") is True
        and report.get("inputs_unchanged") is True
        and report.get("negative_control_detected") is True
        and report.get("clock_multiplier") == multiplier
    ):
        raise ValueError("Incomplete actual ADC clock proof: " + str(path))
    for category in ("inputs", "artifacts"):
        hashes = report.get(category, {})
        if not hashes or any(digest(ROOT / name) != sha for name, sha in hashes.items()):
            raise ValueError("Changed actual ADC clock evidence: " + str(path))
    return report


def parent_binding(text, top):
    """Check the proved device receives the actual master, reset and inputs."""
    parent = re.search(r"^module\s+" + top + r"\b[\s\S]*?^endmodule\b", text, re.M)
    child = (
        None if parent is None else re.search(r"\bAdc7002\s+adc\s*\(([\s\S]*?)\n\s*\);", parent[0])
    )
    if child is None:
        raise ValueError("Missing actual parent ADC")
    ports = {
        name: re.sub(r"\s+", "", expression)
        for name, expression in re.findall(r"\.(\w+)\s*\(([^()]*)\)", child[1])
    }
    if any(
        ports.get(name) != required
        for name, required in {
            "clk": "clk",
            "reset": "reset",
            "io_channels": "io_analogue[63:0]",
            "io_tick": "tickVia",
        }.items()
    ):
        raise ValueError("Disconnected actual ADC clock, reset or channel inputs")
    return ports


def qualify():
    verify()
    if not build.generation_current() or not adc_results.current():
        raise ValueError("Generate and qualify the current ADC digital result profile first")
    video96.generate()
    generated = [build.BBC / "BbcMachine.v", video96.DIRECTORY / "BbcMachine96.v"]
    pattern = r"^module\s+Adc7002\b[\s\S]*?^endmodule\b"
    actual = re.search(pattern, device_rtl(["Adc7002"]), re.M)[0]
    if any(re.search(pattern, source.read_text(), re.M)[0] != actual for source in generated):
        raise ValueError("ADC proof differs from an actual machine variant")
    bindings = [
        parent_binding(source.read_text(), top)
        for source, top in zip(generated, ("BbcMachine", "BbcMachine96"), strict=True)
    ]
    clocks = [build.BBC / f"machine-clock-proof{m}/result.json" for m in (1, 3)]
    clock_reports = [clock_evidence(path, m) for path, m in zip(clocks, (1, 3), strict=True)]
    sampling_path = build.BBC / "adc-sampling/result.json"
    sampling = json.loads(sampling_path.read_text())
    if not (
        sampling.get("passed") is True
        and sampling.get("inputs_unchanged") is True
        and sampling.get("cases_per_clock") == 320
        and sampling.get("observations_per_clock") == 7280
        and sampling.get("negative_control", {}).get("negative_control_detected") is True
        and sampling.get("clocks")
        and all(item.get("passed") is True for item in sampling["clocks"])
        and sampling.get("artifacts")
        and all(digest(ROOT / name) == sha for name, sha in sampling["artifacts"].items())
        and sampling.get("fixture_sha256") == digest(sampling_path.parent / "sampling.tsv")
    ):
        raise ValueError("Current independent dynamic ADC comparison is required")
    contract = ROOT / "sim/formal/BbcAdcContract.v"
    dependencies = [
        Path(__file__),
        contract,
        *generated,
        *clocks,
        sampling_path,
        adc_results.RECEIPT,
    ]
    before = build.hashes(dependencies)
    directory = build.BBC / "adc-state-proof"
    directory.mkdir(exist_ok=True)
    rtl = directory / "miter.v"
    rtl.write_text(observed_module(actual) + contract.read_text())

    def prove(path, label):
        command = (
            f"read_verilog {path}; prep -top AdcContract -flatten; async2sync; opt_clean; "
            "sat -set-init-zero -tempinduct -seq 3 -maxsteps 8 "
            "-prove equivalent 1 -verify -timeout 90"
        )
        result = subprocess.run(
            ["yosys", "-Q", "-T", "-p", command],
            capture_output=True,
            text=True,
            timeout=120,
        )
        (directory / (label + ".log")).write_text(result.stdout + result.stderr)
        return result

    positive = prove(rtl, "proof")
    negatives = []
    for name, target, wrong in (
        ("wrong-duration", "14'h0fa0", "14'h0fa1"),
        (
            "wrong-completion-channel",
            "assign _zz_sample_1 = control[1 : 0];",
            "assign _zz_sample_1 = control[1 : 0] ^ 2'b01;",
        ),
    ):
        if actual.count(target) != 1:
            raise ValueError("Missing actual ADC negative-control target: " + name)
        path = directory / (name + ".v")
        path.write_text(observed_module(actual.replace(target, wrong)) + contract.read_text())
        result = prove(path, name)
        negatives.append(
            {"name": name, "detected": result.returncode != 0 and "proof did fail" in result.stderr}
        )
    verify()
    stable = (
        before == build.hashes(dependencies)
        and build.generation_current()
        and adc_results.current()
    )
    report = {
        "passed": positive.returncode == 0
        and stable
        and all(item["detected"] for item in negatives),
        "inputs": before,
        "inputs_unchanged": stable,
        "negative_controls": negatives,
        "parent_clocks": [item["connections"] for item in clock_reports],
        "parent_bindings": bindings,
        "independent_semantics": {
            "result_values": adc_results.CASES,
            "dynamic_cases_per_clock": sampling["cases_per_clock"],
            "dynamic_observations_per_clock": sampling["observations_per_clock"],
            "result_profile": adc_results.RESULT_PROFILE,
        },
        "artifacts": build.hashes(list(directory.glob("*.v")) + list(directory.glob("*.log"))),
        "scope": "Temporal induction of the exact actual ADC module against an independent elapsed-time digital contract after the declared reset. Arbitrary tick, read, write, address, data and all 64 channel-input bits; cancellation, restart, completion, register retention, acknowledgement and all four EOC/read-race outputs. Exact module binding and separately proved actual 1 MHz parent connections at 32/96 MHz. Existing independent C exhaustive result checks and original-scheduler dynamic comparisons bind the declared digital semantics.",
        "limits": "Universal local digital-contract proof, not universal proof of the original emulator, CPU, VIA, entire machine or game. Reset is the deterministic fixture reset; warm BBC reset semantics are not established. Fixed 4/10 ms timing, completion-time sampling and deterministic completion collisions are declared digital approximations; physical analogue behavior is excluded.",
    }
    write_json(directory / "result.json", report)
    print(f"Actual ADC digital state proof: {report['passed']}", flush=True)
    if not report["passed"]:
        raise ValueError("ADC digital state proof failed; see retained logs")
    return report


if __name__ == "__main__":
    qualify()
