"""Prove selected actual VIA T2 state against elapsed-up byte counters."""

import json
import re
import shutil
import subprocess
from pathlib import Path

from tools.validation.common import ROOT, digest, write_json

from . import build, bus_binding_proof, via_timer1_state_proof, video96

STATE = {
    "timer2": 16,
    "latch2": 16,
    "armed2": 1,
    "start2": 1,
    "reload2": 1,
    "pulseUnderflow": 1,
    "acr": 8,
    "flags": 7,
    "pb6Low": 1,
    "pb6Endpoint": 1,
    "pulseRequest": 1,
    "previousPhi2": 1,
    "pulseLoadPending": 1,
}


def observed(actual):
    """Expose existing state and bus signals without injecting or constraining it."""
    sequential = set(re.findall(r"\b(\w+)(?:\s*\[[^\]]+\])?\s*<=", actual))
    if sequential != via_timer1_state_proof.INVENTORY:
        raise ValueError("Changed VIA sequential-state inventory")
    for name, bits in STATE.items():
        width = r"\s+" if bits == 1 else rf"\s+\[{bits - 1}:0\]\s+"
        if len(re.findall(r"\breg" + width + name + r"\s*;", actual)) != 1:
            raise ValueError("Changed T2 state binding: " + name)
    if len(re.findall(r"\breg\s+\[15:0\]\s+busTimer2\s*;", actual)) != 1:
        raise ValueError("Changed T2 bus-count binding")
    if len(re.findall(r"\bwire\s+serial_io_timerUnderflow\s*;", actual)) != 1:
        raise ValueError("Changed T2 serial-underflow binding")
    header = actual.index(");")
    end = actual.rindex("endmodule")
    return (
        actual[:header]
        + ", output wire [49:0] proofTimerState, output wire [15:0] proofTimerBus, output wire proofUnderflow\n"
        + actual[header:end]
        + "assign proofTimerState={timer2,latch2,armed2,start2,reload2,pulseUnderflow,acr,flags[5],pb6Low,pb6Endpoint,pulseRequest,previousPhi2,pulseLoadPending};\n"
        + "assign proofTimerBus=busTimer2;\nassign proofUnderflow=serial_io_timerUnderflow;\nendmodule\n"
    )


def supporting_evidence(path):
    receipt = json.loads(path.read_text())
    if (
        receipt.get("passed") is not True
        or receipt.get("inputs_unchanged") is not True
        or receipt.get("cases") != 1536
        or receipt.get("observations") != 83968
        or len(receipt.get("periods", [])) != 2
        or {p.get("clock_hz") for p in receipt["periods"]} != {32000000, 96000000}
        or any(p.get("passed") is not True for p in receipt["periods"])
        or receipt.get("negative_control", {}).get("detected") is not True
        or receipt.get("universal_via_equivalence") is not False
        or receipt.get("universal_game_equivalence") is not False
    ):
        raise ValueError("Incomplete independent T2 pulse/load evidence")
    for category in ("sources", "artifacts"):
        hashes = receipt.get(category, {})
        if not hashes or any(digest(ROOT / name) != sha for name, sha in hashes.items()):
            raise ValueError("Changed T2 pulse/load evidence")
    return receipt


def qualify():
    if not build.generation_current():
        raise ValueError("Generate the current machine before proving T2")
    generation96 = video96.generate()
    sources = [build.BBC / "BbcMachine.v", video96.DIRECTORY / "BbcMachine96.v"]
    modules = [bus_binding_proof.module(p.read_text(), "Via6522") for p in sources]
    if modules[0] != modules[1]:
        raise ValueError("Actual VIA components differ between master clocks")
    support = build.BBC / "via-pulse-load/result.json"
    evidence = supporting_evidence(support)
    for hz in (32, 96):
        if (
            bus_binding_proof.module(
                (build.BBC / f"via-pulse-load/native{hz}/devices.v").read_text(), "Via6522"
            )
            != modules[0]
        ):
            raise ValueError("Pulse/load histories used a different actual VIA")
    contract = ROOT / "sim/formal/BbcViaTimer2Contract.v"
    dependencies = [
        Path(__file__),
        Path(via_timer1_state_proof.__file__),
        ROOT / "rtl/bbc/Via6522.scala",
        contract,
        Path(bus_binding_proof.__file__),
        *sources,
        support,
    ]
    before = build.hashes(dependencies)
    directory = build.BBC / "via-timer2-state-proof"
    directory.mkdir(exist_ok=True)
    actual = modules[0]
    children = "\n".join(
        bus_binding_proof.module(sources[0].read_text(), name)
        for name in ("ViaControlPins", "ViaSerial")
    )

    def prove(text, name):
        model = directory / (name + ".v")
        graph = directory / (name + ".json")
        model.write_text(observed(text) + children + contract.read_text())
        command = (
            f"read_verilog -formal -sv {model}; prep -top ViaTimer2Contract -flatten; "
            f"async2sync; opt_clean; write_json {graph}; "
            "sat -set-assumes -set-init-zero -tempinduct -seq 3 -maxsteps 12 "
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
            "previous-pulse-load",
            "assign timer2Tick = (acr[5] ? ((phi1Start && pulseRequest) && (! pulseLoadPending)) : io_tick);",
            "assign timer2Tick = (acr[5] ? (phi1Start && pulseRequest) : io_tick);",
        ),
        (
            "wrong-borrow",
            "timer2 <= (timer2 - 16'h0001);",
            "timer2 <= {timer2[15:8],timer2[7:0] - 8'd1};",
        ),
        ("wrong-sample", "pb6Endpoint <= pb6Low;", "pb6Endpoint <= io_portBIn[6];"),
        (
            "wrong-reload",
            "timer2[7 : 0] <= latch2[7 : 0];",
            "timer2[7 : 0] <= latch2[7 : 0] + 8'd1;",
        ),
    ):
        required = 2 if name == "wrong-reload" else 1
        if actual.count(correct) != required:
            raise ValueError("Changed actual T2 fault target: " + name)
        result = prove(actual.replace(correct, wrong), name)
        negatives.append(
            {"name": name, "detected": result.returncode != 0 and "proof did fail" in result.stderr}
        )
    via_timer1_state_proof.initial_reset_assumption(
        json.loads((directory / "proof.json").read_text()), "ViaTimer2Contract"
    )
    if contract.read_text().count("assume(reset)") != 1:
        raise ValueError("Changed T2 initial-reset assumptions")
    stable = (
        before == build.hashes(dependencies)
        and build.generation_current()
        and generation96 == video96.snapshot()
        and evidence == supporting_evidence(support)
    )
    receipt = {
        "passed": positive.returncode == 0
        and "Induction step proven: SUCCESS!" in positive.stdout
        and stable
        and all(n["detected"] for n in negatives),
        "inputs": before,
        "inputs_unchanged": stable,
        "state_bits": 50,
        "negative_controls": negatives,
        "tools": {
            "yosys": subprocess.check_output(["yosys", "-V"], text=True).strip(),
            "yosys_sha256": digest(Path(shutil.which("yosys"))),
        },
        "artifacts": build.hashes(
            [*directory.glob("*.v"), *directory.glob("*.log"), *directory.glob("*.json")]
        ),
        "scope": "Temporal induction of 50 selected T2 counter/latch, arm/start/reload, ACR, T2 IFR, PHI2/PB6 sample and pulse/load/underflow state bits, forwarded bus count, register 8/9 byte reads and serial-underflow output against independent elapsed-up byte counters. All shift classes, ordinary and PB6 counting, arbitrary subsequent reset/tick/PHI2/GPIO/control/register inputs. No state injection, one initial reset assumption. Actual identical VIA modules in both parent machines; separate unchanged-model pulse/load tests.",
        "limits": "Induction against the stated contract; independent finite reference tests do not universally prove that contract equivalent to original silicon. Other VIA state, IRQ masks/routing, shift data/control, CPU histories and original warm reset remain separate obligations. Not universal VIA or whole game equivalence.",
        "universal_via_equivalence": False,
        "universal_game_equivalence": False,
    }
    receipt["artifacts"].pop(str((directory / "result.json").relative_to(ROOT)), None)
    write_json(directory / "result.json", receipt)
    print(f"Actual VIA T2 temporal proof: {receipt['passed']}", flush=True)
    if not receipt["passed"]:
        raise ValueError("T2 temporal proof failed; all models/logs retained")
    return receipt


if __name__ == "__main__":
    qualify()
