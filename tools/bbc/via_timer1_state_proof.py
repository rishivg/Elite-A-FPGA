"""Prove the actual VIA T1 state against an elapsed-interval contract."""

import json
import re
import shutil
import subprocess
from pathlib import Path

from tools.validation.common import ROOT, digest, write_json

from . import build, bus_binding_proof, video96

STATE = {
    "timer1": 16,
    "latch1": 16,
    "armed1": 1,
    "reload1": 1,
    "start1": 1,
    "pb7": 1,
    "acr": 8,
    "flags": 7,
}
INVENTORY = {
    "acr",
    "armed1",
    "armed2",
    "ddra",
    "ddrb",
    "enabled",
    "flags",
    "inputA",
    "inputB",
    "latch1",
    "latch2",
    "ora",
    "orb",
    "pb6Endpoint",
    "pb6Low",
    "pb7",
    "previousCa1",
    "previousCa2",
    "previousCb1",
    "previousCb2",
    "previousPhi2",
    "pulseRequest",
    "pulseUnderflow",
    "pulseLoadPending",
    "reload1",
    "reload2",
    "serialWriteData",
    "serialWritePending",
    "start1",
    "start2",
    "timer1",
    "timer2",
}


def observed(actual):
    """Expose existing T1 signals only; never write or constrain DUT state."""
    sequential = set(re.findall(r"\b(\w+)(?:\s*\[[^\]]+\])?\s*<=", actual))
    if sequential != INVENTORY:
        raise ValueError("Changed VIA sequential-state inventory")
    for name, bits in STATE.items():
        width = r"\s+" if bits == 1 else rf"\s+\[{bits - 1}:0\]\s+"
        if len(re.findall(r"\breg" + width + name + r"\s*;", actual)) != 1:
            raise ValueError("Changed T1 state binding: " + name)
    if len(re.findall(r"\breg\s+\[15:0\]\s+busTimer1\s*;", actual)) != 1:
        raise ValueError("Changed T1 bus-count binding")
    header = actual.index(");")
    end = actual.rindex("endmodule")
    return (
        actual[:header]
        + ", output wire [44:0] proofTimerState, output wire [15:0] proofTimerBus\n"
        + actual[header:end]
        + "assign proofTimerState={timer1,latch1,armed1,reload1,start1,pb7,acr,flags[6]};\n"
        + "assign proofTimerBus=busTimer1;\nendmodule\n"
    )


def supporting_evidence(path):
    receipt = json.loads(path.read_text())
    if (
        receipt.get("passed") is not True
        or receipt.get("inputs_unchanged") is not True
        or receipt.get("cases") != 896
        or receipt.get("operations") != 79488
        or len(receipt.get("periods", [])) != 2
        or {p.get("clock_hz") for p in receipt["periods"]} != {32000000, 96000000}
        or any(p.get("passed") is not True for p in receipt["periods"])
        or receipt.get("negative_control", {}).get("detected") is not True
        or receipt.get("universal_via_equivalence") is not False
        or receipt.get("universal_game_equivalence") is not False
    ):
        raise ValueError("Incomplete independent timer-history evidence")
    for category in ("sources", "artifacts"):
        hashes = receipt.get(category, {})
        if not hashes or any(digest(ROOT / name) != sha for name, sha in hashes.items()):
            raise ValueError("Changed timer-history evidence")
    return receipt


def initial_reset_assumption(graph, top="ViaTimer1Contract"):
    """Admit all input histories after reset; reject extra restrictions."""
    module = graph["modules"][top]
    cells = module["cells"]
    assumptions = [cell for cell in cells.values() if cell["type"] == "$assume"]
    if (
        len(assumptions) != 1
        or assumptions[0]["connections"]["A"] != module["ports"]["reset"]["bits"]
    ):
        raise ValueError("T1 proof must assume only initial reset")
    initial = [cell["connections"]["Y"] for cell in cells.values() if cell["type"] == "$initstate"]
    if len(initial) != 1:
        raise ValueError("Missing unique T1 initial-state signal")
    enabled = assumptions[0]["connections"]["EN"]
    if enabled == initial[0]:
        return
    drivers = [cell for cell in cells.values() if cell.get("connections", {}).get("Y") == enabled]
    if len(drivers) != 1 or drivers[0]["type"] != "$and":
        raise ValueError("Conditional T1 reset assumption")
    pins = drivers[0]["connections"]
    if not (
        (pins["A"] == ["1"] and pins["B"] == initial[0])
        or (pins["B"] == ["1"] and pins["A"] == initial[0])
    ):
        raise ValueError("Conditional T1 reset assumption")


def qualify():
    if not build.generation_current():
        raise ValueError("Generate the actual current machine before proving T1")
    generation96 = video96.generate()
    sources = [build.BBC / "BbcMachine.v", video96.DIRECTORY / "BbcMachine96.v"]
    modules = [bus_binding_proof.module(p.read_text(), "Via6522") for p in sources]
    if modules[0] != modules[1]:
        raise ValueError("Actual VIA components differ between master clocks")
    support = build.BBC / "via-timer-history/result.json"
    evidence = supporting_evidence(support)
    if (
        bus_binding_proof.module(
            (build.BBC / "via-timer-history/actual/devices.v").read_text(), "Via6522"
        )
        != modules[0]
    ):
        raise ValueError("Independent timer histories used a different actual VIA")
    contract = ROOT / "sim/formal/BbcViaTimer1Contract.v"
    dependencies = [
        Path(__file__),
        ROOT / "rtl/bbc/Via6522.scala",
        contract,
        Path(bus_binding_proof.__file__),
        *sources,
        support,
    ]
    before = build.hashes(dependencies)
    directory = build.BBC / "via-timer1-state-proof"
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
            f"read_verilog -formal -sv {model}; prep -top ViaTimer1Contract -flatten; "
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
            "previous-free-run",
            "assign timer1OneShot = ((! acr[6]) || ((io_write && (io_address == 4'b1011)) && (! io_data[6])));",
            "assign timer1OneShot = (! acr[6]);",
        ),
        ("wrong-relatch", "timer1 <= latch1;", "timer1 <= latch1 + 16'd1;"),
        ("wrong-pb7", "pb7 <= (! pb7);", "pb7 <= pb7;"),
    ):
        if actual.count(correct) != 1:
            raise ValueError("Missing unique actual T1 fault target: " + name)
        result = prove(actual.replace(correct, wrong), name)
        negatives.append(
            {"name": name, "detected": result.returncode != 0 and "proof did fail" in result.stderr}
        )
    graph = json.loads((directory / "proof.json").read_text())
    # The contract admits every input history after its single initial reset.
    initial_reset_assumption(graph)
    if contract.read_text().count("assume(reset)") != 1:
        raise ValueError("Changed T1 initial-reset assumptions")
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
        "state_bits": 45,
        "negative_controls": negatives,
        "tools": {
            "yosys": subprocess.check_output(["yosys", "-V"], text=True).strip(),
            "yosys_sha256": digest(Path(shutil.which("yosys"))),
        },
        "artifacts": build.hashes(
            [*directory.glob("*.v"), *directory.glob("*.log"), *directory.glob("*.json")]
        ),
        "scope": "Temporal induction of all T1 counter/latch, arm/start/reload, PB7, ACR and T1 IFR state, forwarded 16-bit bus count and register 4..7 byte reads against an independent elapsed-up-interval contract. Arbitrary ticks, PHI2, external GPIO/control levels, register bytes, reads/writes, latch changes and ACR transitions after initial declared reset; actual identical VIA modules at both master clocks. Independent BBC history tests validate the specified bus semantics separately.",
        "limits": "Selected 45 T1-relevant state bits and timer byte reads, not every VIA bit or IRQ mask/routing. Declared design reset, not proof of original warm-reset semantics. Actual parent clocks have a separate obligation. Not universal VIA, CPU or whole game equivalence.",
        "universal_via_equivalence": False,
        "universal_game_equivalence": False,
    }
    receipt["artifacts"].pop(str((directory / "result.json").relative_to(ROOT)), None)
    write_json(directory / "result.json", receipt)
    print(f"Actual VIA T1 temporal proof: {receipt['passed']}", flush=True)
    if not receipt["passed"]:
        raise ValueError("T1 temporal proof failed; models and logs retained")
    return receipt


if __name__ == "__main__":
    qualify()
