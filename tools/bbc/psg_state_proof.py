"""Prove all BBC PSG state and digital outputs against elapsed intervals."""

import json
import re
import subprocess
from pathlib import Path

from tools.validation.common import ROOT, digest, write_json

from . import build, bus_binding_proof, video96

STATE = {
    **{f"tonePeriod_{i}": 10 for i in range(3)},
    **{f"toneVol_{i}": 4 for i in range(3)},
    "noiseVol": 4,
    "noiseCtrl": 3,
    "latchedReg": 2,
    "latchedVol": 1,
    "_zz_noiseTick": 7,
    **{f"toneCnt_{i}": 10 for i in range(3)},
    **{f"toneFf_{i}": 1 for i in range(3)},
    "lfsr": 15,
    "noiseCnt": 11,
    "noiseFf": 1,
    "acc": 9,
}


def observed(actual):
    if sum(STATE.values()) != 128:
        raise ValueError("Changed declared PSG state width")
    sequential = set(re.findall(r"\b(\w+)(?:\s*\[[^\]]+\])?\s*<=", actual))
    if sequential != set(STATE):
        raise ValueError("Changed actual PSG sequential-state inventory")
    for name, bits in STATE.items():
        width = r"\s+" if bits == 1 else rf"\s+\[{bits - 1}:0\]\s+"
        if len(re.findall(r"\breg" + width + re.escape(name) + r"\s*;", actual)) != 1:
            raise ValueError("Changed actual PSG state binding: " + name)
    header = actual.index(");")
    return (
        actual[:header]
        + ", output wire [127:0] proofState\n"
        + actual[header : actual.rindex("endmodule")]
        + "assign proofState={"
        + ",".join(STATE)
        + "};\nendmodule\n"
    )


def supporting_evidence(path):
    report = json.loads(path.read_text())
    if report.get("passed") is not True or report.get("coverage_complete") is not True:
        raise ValueError("Incomplete original PSG evidence: " + str(path))
    for category in ("sources", "artifacts"):
        hashes = report.get(category, {})
        if not hashes or any(digest(ROOT / name) != sha for name, sha in hashes.items()):
            raise ValueError("Changed original PSG evidence: " + str(path))
    captures = report.get("captures", {})
    if set(captures) != {"levels", "pins"} or any(
        item.get("passed") is not True
        or item.get("different_samples") != 0
        or item.get("samples") != report.get("fixture_cycles", 0) // 64
        or item.get("samples", 0) <= 0
        for item in captures.values()
    ):
        raise ValueError("Incomplete original PSG sample/pin comparison")
    return report


def qualify():
    if not build.generation_current():
        raise ValueError("Generate the actual whole machine before proving sound")
    generation96 = video96.generate()
    sources = [build.BBC / "BbcMachine.v", video96.DIRECTORY / "BbcMachine96.v"]
    modules = [bus_binding_proof.module(source.read_text(), "Psg") for source in sources]
    if modules[0] != modules[1]:
        raise ValueError("Actual PSG children differ between master clocks")
    standalone = build.BBC / "audio-interval/devices.v"
    if bus_binding_proof.module(standalone.read_text(), "Psg") != modules[0]:
        raise ValueError("Original sound sweeps used a different actual PSG")
    support = [
        build.BBC / ("psg-sweep-" + name) / "result.json"
        for name in ("tones", "noise", "linked", "linked-zero", "clock-writes", "noise-controls")
    ]
    evidence = [supporting_evidence(path) for path in support]
    bindings = []
    for source, top in zip(sources, ("BbcMachine", "BbcMachine96"), strict=True):
        audio = bus_binding_proof.module(source.read_text(), "BbcAudio")
        ports = bus_binding_proof.ports(audio, "Psg", "audio_chip")
        required = {
            "clk": "clk",
            "reset": "reset",
            "audio_newClockEnable": "audio_newClockEnable",
            "io_write": "audio_bus_io_write",
            "io_data": "audio_bus_io_acceptedData[7:0]",
        }
        if any(
            re.sub(r"\s+", "", ports.get(name, "")) != value for name, value in required.items()
        ):
            raise ValueError("Changed actual PSG parent connection")
        if (
            len(re.findall(r"assign\s+audio_newClockEnable\s*=\s*\(1'b1 && io_clock32\);", audio))
            != 1
        ):
            raise ValueError("Changed actual sound clock-enable binding")
        parent = bus_binding_proof.module(source.read_text(), top)
        outer = bus_binding_proof.ports(parent, "BbcAudio", "audio")
        if any(
            outer.get(name, "").strip() != value
            for name, value in {"clk": "clk", "reset": "reset", "io_clock32": "clock32"}.items()
        ):
            raise ValueError("Changed actual machine audio connection")
        bindings.append({"machine": top, "psg_ports": ports, "audio_ports": outer})
    contract = ROOT / "sim/formal/BbcPsgContract.v"
    dependencies = [
        Path(__file__),
        Path(bus_binding_proof.__file__),
        *sources,
        standalone,
        contract,
        *support,
    ]
    before = build.hashes(dependencies)
    directory = build.BBC / "psg-state-proof"
    directory.mkdir(exist_ok=True)
    actual = modules[0]

    def prove(text, name):
        model = directory / (name + ".v")
        model.write_text(observed(text) + contract.read_text())
        command = (
            f"read_verilog -formal -sv {model}; prep -top PsgContract -flatten; "
            "memory_map; opt; async2sync; opt_clean; "
            "sat -set-assumes -set-init-zero -tempinduct -seq 3 -maxsteps 8 "
            "-prove equivalent 1 -verify -timeout 90"
        )
        result = subprocess.run(
            ["yosys", "-Q", "-T", "-p", command], capture_output=True, text=True, timeout=120
        )
        (directory / (name + ".log")).write_text(result.stdout + result.stderr)
        return result

    positive = prove(actual, "proof")
    negatives = []
    for name, correct, wrong in (
        (
            "wrong-tone-reload",
            "toneCnt_0 <= (generatorPeriods_0 - 10'h001);",
            "toneCnt_0 <= (generatorPeriods_0 - 10'h002);",
        ),
        (
            "wrong-noise-seed",
            "if(noiseReset) begin\n          lfsr <= 15'h4000;",
            "if(noiseReset) begin\n          lfsr <= 15'h4001;",
        ),
        ("wrong-linked-zero", "(noiseRate == 10'h0) ? 11'h001", "(noiseRate == 10'h0) ? 11'h000"),
    ):
        if actual.count(correct) != 1:
            raise ValueError("Missing actual PSG mutation target: " + name)
        result = prove(actual.replace(correct, wrong), name)
        negatives.append(
            {"name": name, "detected": result.returncode != 0 and "proof did fail" in result.stderr}
        )
    stable = (
        before == build.hashes(dependencies)
        and build.generation_current()
        and generation96 == video96.snapshot()
    )
    # Recheck every original producer and observation byte at completion too.
    stable = stable and evidence == [supporting_evidence(path) for path in support]
    report = {
        "passed": positive.returncode == 0 and stable and all(n["detected"] for n in negatives),
        "inputs": before,
        "inputs_unchanged": stable,
        "state_bits": sum(STATE.values()),
        "parent_bindings": bindings,
        "negative_controls": negatives,
        "independent_samples_and_pin_vectors": sum(
            r["captures"]["levels"]["samples"] for r in evidence
        ),
        "artifacts": build.hashes([*directory.glob("*.v"), *directory.glob("*.log")]),
        "solver": subprocess.check_output(["yosys", "-V"], text=True).strip(),
        "scope": "Temporal induction of every one of 128 actual PSG state bits and all sample, tone/noise pin and PDM outputs against independent elapsed intervals. Arbitrary clock enables and byte writes after shared reset, including latch/data protocol, independently programmed tone periods, coincident generator/write boundaries, zero periods, all noise controls, seed-before-shift ordering, attenuation and PDM accumulation. Both actual parents contain the identical child and retain its clock/reset/write/data bindings. Six byte-sealed unchanged-original sound sweeps separately check the declared digital semantics.",
        "limits": "Universal local declared digital PSG contract, not a universal proof of the original emulator, sound-bus acceptance, complete CPU/game or physical sound. The existing original comparisons have finite command schedules; analogue levels and independent emulator floating-point audio remain outside this integer fixture.",
    }
    write_json(directory / "result.json", report)
    print(f"Actual PSG temporal proof: {report['passed']}", flush=True)
    if not report["passed"]:
        raise ValueError("PSG temporal proof failed; logs and private mutations retained")
    return report


if __name__ == "__main__":
    qualify()
