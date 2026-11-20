"""Prove that a disabled CPU edge preserves every register with NMI inactive."""

import json
import subprocess
from pathlib import Path

from tools.validation.common import ROOT, write_json

from . import build, prepare

DIRECTORY = build.BBC / "cpu-idle-proof"


def qualify():
    prepare.main()
    DIRECTORY.mkdir(parents=True, exist_ok=True)
    sources = [
        Path(__file__),
        ROOT / "tools/bbc/prepare.py",
        ROOT / "tools/bbc/cpu_bus.py",
        ROOT / "rtl/bbc/BbcCpu.v",
        build.BBC / "ArletCpuEnabled.v",
        ROOT / "third_party/6502/cpu.v",
        ROOT / "third_party/6502/ALU.v",
    ]
    before = build.hashes(sources)
    version = subprocess.check_output(["yosys", "-V"], text=True).strip()
    saved = build.load_receipt("cpu-idle-proof/result.json")
    if (
        saved.get("passed") is True
        and saved.get("inputs") == before
        and saved.get("yosys") == version
        and saved.get("excluded_register_bits") == 0
        and saved.get("artifacts")
        and saved["artifacts"] == build.hashes([ROOT / p for p in saved["artifacts"]])
    ):
        print(
            f"Verified CPU idle preservation: all {saved['register_bits']} register bits",
            flush=True,
        )
        return saved
    model = DIRECTORY / "registers.json"
    with (DIRECTORY / "prepare.log").open("w") as log:
        subprocess.run(
            [
                "yosys",
                "-Q",
                "-T",
                "-p",
                f"read_verilog rtl/bbc/BbcCpu.v build/bbc/ArletCpuEnabled.v; hierarchy -top BbcCpu; flatten; proc; memory_map; opt; dffunmap; write_json {model}",
            ],
            cwd=ROOT,
            stdout=log,
            stderr=subprocess.STDOUT,
            check=True,
            timeout=60,
        )
    graph = json.loads(model.read_text())
    module = graph["modules"]["BbcCpu"]
    state, following = [], []
    nmi_previous = module["netnames"]["core.NMI_1"]["bits"]
    sampler_verified = False
    for name, cell in list(module["cells"].items()):
        kind = cell["type"]
        if kind not in {"$dff", "$adff"}:
            if any(token in kind.lower() for token in ["ff", "latch", "mem"]):
                raise ValueError("Unrecognized CPU state primitive: " + kind)
            continue
        ports = cell["connections"]
        if (
            ports["CLK"] != module["ports"]["clk"]["bits"]
            or int(cell["parameters"]["CLK_POLARITY"], 2) != 1
        ):
            raise ValueError("Unexpected CPU register clock")
        if kind == "$adff" and (
            ports["ARST"] != module["ports"]["reset"]["bits"]
            or int(cell["parameters"]["ARST_POLARITY"], 2) != 1
        ):
            raise ValueError("Unexpected asynchronous CPU reset")
        if ports["Q"] == nmi_previous:
            sampler_verified = kind == "$dff" and ports["D"] == module["ports"]["nmi"]["bits"]
        if len(ports["Q"]) != len(ports["D"]):
            raise ValueError("CPU register width mismatch")
        state.extend(ports["Q"])
        following.extend(ports["D"])
        del module["cells"][name]
    if not sampler_verified or len(nmi_previous) != 1 or not state or len(state) != len(set(state)):
        raise ValueError("Incomplete CPU register inventory or NMI sampler")
    # Every physical register becomes an unconstrained symbolic input. Its
    # exact synthesized D expression remains intact; no state bit is excluded.
    for wire in module["netnames"].values():
        wire["attributes"].pop("init", None)
    bit = (
        max(b for wire in module["netnames"].values() for b in wire["bits"] if isinstance(b, int))
        + 1
    )
    module["ports"]["state_in"] = {"direction": "input", "bits": state}
    module["ports"]["holds"] = {"direction": "output", "bits": [bit]}
    module["netnames"]["nmi_previous"] = {"hide_name": 0, "bits": nmi_previous, "attributes": {}}
    width = format(len(state), "032b")
    module["cells"]["hold_compare"] = {
        "hide_name": 0,
        "type": "$eq",
        "attributes": {},
        "parameters": {
            "A_SIGNED": "0" * 32,
            "B_SIGNED": "0" * 32,
            "A_WIDTH": width,
            "B_WIDTH": width,
            "Y_WIDTH": format(1, "032b"),
        },
        "port_directions": {"A": "input", "B": "input", "Y": "output"},
        "connections": {"A": state, "B": following, "Y": [bit]},
    }
    symbolic = DIRECTORY / "symbolic.json"
    write_json(symbolic, graph)
    with (DIRECTORY / "proof.log").open("w") as log:
        result = subprocess.run(
            [
                "yosys",
                "-Q",
                "-T",
                "-p",
                f"read_json {symbolic}; hierarchy -top BbcCpu; opt_clean; sat -set enable 0 -set reset 0 -set warm_reset 0 -set nmi 0 -set nmi_previous 0 -prove holds 1 -verify",
            ],
            cwd=ROOT,
            stdout=log,
            stderr=subprocess.STDOUT,
            timeout=60,
        )
    unchanged = before == build.hashes(sources)
    receipt = {
        "passed": result.returncode == 0 and unchanged,
        "register_bits": len(state),
        "excluded_register_bits": 0,
        "inputs": before,
        "inputs_unchanged": unchanged,
        "artifacts": build.hashes(
            [model, symbolic, DIRECTORY / "prepare.log", DIRECTORY / "proof.log"]
        ),
        "yosys": version,
        "scope": "All synthesized CPU register D inputs equal Q when enable=0, reset=0, warm_reset=0, NMI=0 and previous NMI sample=0. Every other state bit, read-data value and IRQ level is symbolic. The verified NMI sampler directly stores NMI, so its zero condition is invariant when NMI remains inactive through reset and execution.",
        "limits": "Disabled-edge preservation only. This does not prove enabled instruction semantics, reset behavior, NMI/IRQ timing, or whole-machine idle compaction. Whole-game captures still execute every master edge.",
    }
    write_json(DIRECTORY / "result.json", receipt)
    if not receipt["passed"]:
        raise ValueError("CPU disabled-edge preservation failed; proof artifacts retained")
    print(f"CPU idle preservation: all {len(state)} register bits; zero exclusions", flush=True)
    return receipt


if __name__ == "__main__":
    qualify()
