"""Prove sound acceptance deadlines, data retention and integration bindings."""

import json
import re
import subprocess
from pathlib import Path

from tools.validation.common import ROOT, digest, write_json

from . import build, bus_binding_proof, video96


def observed(actual):
    state = {"previousData": 8, "previousEnabled": 1, "delay": 8}
    if set(re.findall(r"\b(\w+)(?:\s*\[[^\]]+\])?\s*<=", actual)) != set(state):
        raise ValueError("Changed actual sound-bus sequential-state inventory")
    for name, bits in state.items():
        width = r"\s+" if bits == 1 else rf"\s+\[{bits - 1}:0\]\s+"
        if len(re.findall(r"\breg" + width + name + r"\s*;", actual)) != 1:
            raise ValueError("Changed actual sound-bus state binding: " + name)
    header = actual.index(");")
    return (
        actual[:header]
        + ", output wire [16:0] proofState\n"
        + actual[header : actual.rindex("endmodule")]
        + "assign proofState={previousData,previousEnabled,delay};\nendmodule\n"
    )


def supporting_evidence(path, multiplier):
    report = json.loads(path.read_text())
    if report.get("passed") is not True or report.get("master_clock_hz") != multiplier * 32000000:
        raise ValueError("Incomplete original sound-bus evidence")
    artifacts = report.get("artifacts", {})
    if not artifacts or any(digest(ROOT / name) != sha for name, sha in artifacts.items()):
        raise ValueError("Changed sound-bus producers")
    for name, suffix in (("levels", ""), ("pins", ".pins"), ("writes", ".writes")):
        capture = report.get("captures", {}).get(name, {})
        if capture.get("passed") is not True:
            raise ValueError("Incomplete original sound-bus captures")
        if name == "writes":
            if capture.get("events") != 27:
                raise ValueError("Incomplete original sound-bus acceptance events")
        elif capture.get("samples") != 8000 or capture.get("different_samples") != 0:
            raise ValueError("Incomplete original sound-bus sample/pin comparison")
        for backend in ("reference", "dut"):
            observation = path.parent / (backend + ".u8" + suffix)
            if digest(observation) != capture.get(backend + "_sha256"):
                raise ValueError("Changed sound-bus observation bytes")
            if (name == "writes" and len(observation.read_bytes().splitlines()) != 27) or (
                name != "writes" and observation.stat().st_size != 8000
            ):
                raise ValueError("Incomplete actual sound-bus observation length")
        if capture.get("reference_sha256") != capture.get("dut_sha256"):
            raise ValueError("Original sound-bus observations differ")
    return report


def qualify():
    if not build.generation_current():
        raise ValueError("Generate the actual whole machine before proving sound acceptance")
    generation96 = video96.generate()
    sources = [build.BBC / "BbcMachine.v", video96.DIRECTORY / "BbcMachine96.v"]
    modules = [bus_binding_proof.module(p.read_text(), "BbcSoundBus") for p in sources]
    if modules[0] != modules[1]:
        raise ValueError("Actual sound-bus children differ between master clocks")
    support = [build.BBC / name / "result.json" for name in ("sound-bus", "sound-bus96")]
    evidence = [supporting_evidence(p, m) for p, m in zip(support, (1, 3), strict=True)]
    bindings = []
    for source, support_path in zip(sources, support, strict=True):
        audio = bus_binding_proof.module(source.read_text(), "BbcAudio")
        bus = bus_binding_proof.ports(audio, "BbcSoundBus", "audio_bus")
        chip = bus_binding_proof.ports(audio, "Psg", "audio_chip")
        expected = {
            "clk": "clk",
            "reset": "reset",
            "io_enabled": "io_enabled",
            "io_touch": "io_touch",
            "io_data": "io_data[7:0]",
            "audio_newClockEnable": "audio_newClockEnable",
        }
        if any(re.sub(r"\s+", "", bus.get(n, "")) != value for n, value in expected.items()):
            raise ValueError("Changed actual sound-bus parent input")
        if any(
            bus.get(name) != chip.get(other)
            for name, other in (
                ("io_write", "io_write"),
                ("io_acceptedData", "io_data"),
                ("clk", "clk"),
                ("reset", "reset"),
                ("audio_newClockEnable", "audio_newClockEnable"),
            )
        ):
            raise ValueError("Changed actual sound-bus/PSG integration binding")
        if (
            bus_binding_proof.module((support_path.parent / "devices.v").read_text(), "BbcSoundBus")
            != modules[0]
        ):
            raise ValueError("Original sound-bus comparison used another child")
        bindings.append({"sound_bus": bus, "psg": chip})
    contract = ROOT / "sim/formal/BbcSoundBusContract.v"
    dependencies = [Path(__file__), Path(bus_binding_proof.__file__), *sources, contract, *support]
    before = build.hashes(dependencies)
    directory = build.BBC / "sound-bus-state-proof"
    directory.mkdir(exist_ok=True)

    def prove(text, name):
        model = directory / (name + ".v")
        model.write_text(observed(text) + contract.read_text())
        command = (
            f"read_verilog -formal {model}; prep -top SoundBusContract -flatten; async2sync; opt_clean; "
            "sat -set-assumes -set-init-zero -tempinduct -seq 3 -maxsteps 8 "
            "-prove equivalent 1 -verify -timeout 90"
        )
        result = subprocess.run(
            ["yosys", "-Q", "-T", "-p", command], capture_output=True, text=True, timeout=120
        )
        (directory / (name + ".log")).write_text(result.stdout + result.stderr)
        return result

    actual = modules[0]
    positive = prove(actual, "proof")
    negatives = []
    for name, correct, wrong in (
        (
            "wrong-accepted-data",
            "assign io_acceptedData = previousData;",
            "assign io_acceptedData = io_data;",
        ),
        (
            "wrong-deadline",
            "assign io_write = (previousEnabled && (delay == 8'he0));",
            "assign io_write = (previousEnabled && (delay == 8'hdf));",
        ),
    ):
        if actual.count(correct) != 1:
            raise ValueError("Missing actual sound-bus mutation target: " + name)
        result = prove(actual.replace(correct, wrong), name)
        negatives.append(
            {"name": name, "detected": result.returncode != 0 and "proof did fail" in result.stderr}
        )
    stable = (
        before == build.hashes(dependencies)
        and build.generation_current()
        and generation96 == video96.snapshot()
    )
    stable = stable and evidence == [
        supporting_evidence(p, m) for p, m in zip(support, (1, 3), strict=True)
    ]
    report = {
        "passed": positive.returncode == 0 and stable and all(n["detected"] for n in negatives),
        "inputs": before,
        "inputs_unchanged": stable,
        "state_bits": 17,
        "parent_bindings": bindings,
        "negative_controls": negatives,
        "artifacts": build.hashes([*directory.glob("*.v"), *directory.glob("*.log")]),
        "scope": "Temporal induction of all actual sound-bus state and acceptance outputs against an independent countdown deadline contract. Arbitrary clock enables, WE, touch and data histories after active cold reset: retained scheduled windows, latest preceding data/WE at completion and immediately scheduled subsequent windows. Both actual child bindings feed identical accepted writes/data to the PSG in the same enabled clock domain; original accepted-event, sample and pin comparisons are sealed at both master rates.",
        "limits": "Universal local declared stable-window interface, not universal equivalence of the original emulator, VIA/IC32 outputs, complete game or physical SN76489 acceptance. Supporting original tests use finite schedules. The initial deadline follows the declared active Model B cold-reset pins.",
    }
    write_json(directory / "result.json", report)
    print(f"Actual sound-bus temporal proof: {report['passed']}", flush=True)
    if not report["passed"]:
        raise ValueError("Sound-bus temporal proof failed; private models and logs retained")
    return report


if __name__ == "__main__":
    qualify()
