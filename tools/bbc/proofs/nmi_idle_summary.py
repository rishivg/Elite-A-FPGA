"""Prove every disabled CPU state transition with unrestricted physical NMI."""

import json
import subprocess
from pathlib import Path

from tools.bbc import build
from tools.validation.common import ROOT, write_json


def prepare_graph(model, wrapper, directory):
    raw = directory / "registers.json"
    command = (
        f"read_verilog {wrapper} {model}; hierarchy -top BbcCpu; flatten; proc; "
        f"memory_map; opt; dffunmap; write_json {raw}"
    )
    with (directory / "prepare.log").open("w") as output:
        subprocess.run(
            ["yosys", "-Q", "-T", "-p", command],
            stdout=output,
            stderr=subprocess.STDOUT,
            cwd=ROOT,
            check=True,
            timeout=60,
        )
    graph = json.loads(raw.read_text())
    body = graph["modules"]["BbcCpu"]
    state, following = [], []
    for name, cell in list(body["cells"].items()):
        kind = cell["type"]
        if kind not in {"$dff", "$adff"}:
            if any(token in kind.lower() for token in ("ff", "latch", "mem")):
                raise ValueError("Unrecognized physical state primitive")
            continue
        ports = cell["connections"]
        if (
            ports["CLK"] != body["ports"]["clk"]["bits"]
            or int(cell["parameters"]["CLK_POLARITY"], 2) != 1
        ):
            raise ValueError("Unexpected physical clock")
        if kind == "$adff" and (
            ports["ARST"] != body["ports"]["reset"]["bits"]
            or int(cell["parameters"]["ARST_POLARITY"], 2) != 1
        ):
            raise ValueError("Unexpected asynchronous reset")
        if len(ports["Q"]) != len(ports["D"]):
            raise ValueError("Physical register width mismatch")
        state.extend(ports["Q"])
        following.extend(ports["D"])
        del body["cells"][name]
    if len(state) != 168 or len(set(state)) != 168:
        raise ValueError("Incomplete physical CPU inventory")
    for wire in body["netnames"].values():
        wire["attributes"].pop("init", None)
    fresh = (
        max(
            bit
            for wire in body["netnames"].values()
            for bit in wire["bits"]
            if isinstance(bit, int)
        )
        + 1
    )

    def gate(name, kind, connections):
        nonlocal fresh
        output = fresh
        fresh += 1
        ports = {**connections, "Y": [output]}
        parameters = {"Y_WIDTH": format(1, "032b")}
        for port in connections:
            parameters[port + "_WIDTH"] = format(len(ports[port]), "032b")
            parameters[port + "_SIGNED"] = "0" * 32
        body["cells"][name] = {
            "hide_name": 0,
            "type": kind,
            "attributes": {},
            "parameters": parameters,
            "port_directions": {port: "output" if port == "Y" else "input" for port in ports},
            "connections": ports,
        }
        return [output]

    previous = body["netnames"]["core.NMI_1"]["bits"]
    pending = body["netnames"]["core.NMI_edge"]["bits"]
    unaged = body["netnames"]["core.bbc_nmi_unaged"]["bits"]
    nmi = body["ports"]["nmi"]["bits"]
    if any(len(bits) != 1 or bits[0] not in state for bits in (previous, pending, unaged)):
        raise ValueError("Missing independently clocked NMI state")
    inverse = gate("summary_inverse", "$not", {"A": previous})
    rising = gate("summary_rising", "$and", {"A": nmi, "B": inverse})
    expected = list(state)
    expected[state.index(previous[0])] = nmi[0]
    expected[state.index(pending[0])] = gate("summary_pending", "$or", {"A": pending, "B": rising})[
        0
    ]
    expected[state.index(unaged[0])] = gate("summary_unaged", "$or", {"A": unaged, "B": rising})[0]
    holds = gate("summary_compare", "$eq", {"A": expected, "B": following})
    body["ports"]["state_in"] = {"direction": "input", "bits": state}
    body["ports"]["summary_holds"] = {"direction": "output", "bits": holds}
    symbolic = directory / "symbolic.json"
    write_json(symbolic, graph)
    return symbolic


def qualify():
    directory = build.BBC / "cpu-nmi-idle-summary"
    directory.mkdir(exist_ok=True)
    core = build.BBC / "ArletCpuEnabled.v"
    wrapper = ROOT / "rtl/bbc/BbcCpu.v"
    inputs = [Path(__file__), core, wrapper, ROOT / "third_party/6502/ALU.v"]
    before = build.hashes(inputs)
    faults = (
        ("gated-nmi-sample", "NMI_1 <= NMI;", "if (CE) NMI_1 <= NMI;"),
        (
            "lost-nmi-edge",
            "else if( NMI & ~NMI_1 )\n        NMI_edge <= 1;",
            "else if( NMI & ~NMI_1 )\n        NMI_edge <= 0;",
        ),
    )
    variants = [("positive", core)]
    for name, correct, wrong in faults:
        text = core.read_text()
        if text.count(correct) != 1:
            raise ValueError("Changed actual NMI mutation site")
        path = directory / (name + ".v")
        path.write_text(text.replace(correct, wrong))
        variants.append((name, path))
    results = []
    artifacts = [model for name, model in variants if name != "positive"]
    for name, model in variants:
        case = directory / name
        case.mkdir(exist_ok=True)
        symbolic = prepare_graph(model, wrapper, case)
        command = (
            f"read_json {symbolic}; hierarchy -top BbcCpu; opt_clean; "
            "sat -set enable 0 -set reset 0 -set warm_reset 0 -prove summary_holds 1 -verify "
            + f"-show state_in -show nmi -show summary_holds -dump_json {case / 'counterexample.json'}"
        )
        log = case / "proof.log"
        with log.open("w") as output:
            run = subprocess.run(
                ["yosys", "-Q", "-T", "-p", command],
                stdout=output,
                stderr=subprocess.STDOUT,
                cwd=ROOT,
                timeout=60,
            )
        text = log.read_text()
        accepted = (
            run.returncode == 0 and "no model found: SUCCESS!" in text
            if name == "positive"
            else run.returncode != 0
            and "proof did fail" in text
            and (case / "counterexample.json").is_file()
        )
        results.append({"name": name, "accepted": accepted})
        artifacts.extend(
            case / filename
            for filename in ("registers.json", "symbolic.json", "prepare.log", "proof.log")
        )
        if (case / "counterexample.json").is_file():
            artifacts.append(case / "counterexample.json")
        print(results[-1], flush=True)
    stable = before == build.hashes(inputs)
    report = {
        "passed": stable and all(row["accepted"] for row in results),
        "inputs": before,
        "inputs_unchanged": stable,
        "artifacts": build.hashes(artifacts),
        "register_bits": 168,
        "excluded_register_bits": 0,
        "proofs": results,
        "tools": {"yosys": subprocess.check_output(["yosys", "-V"], text=True).strip()},
        "scope": "Every actual physical CPU state bit on a disabled master edge, with reset and warm-reset inactive. NMI, its previous sample, every other state bit, IRQ and read-data are unrestricted. The previous NMI sample becomes the input pin; pending and unaged NMI become their old value OR a rising edge. All other 165 state bits hold. Formal D/Q state cuts are symbolic verification inputs, not simulation state injection.",
        "derivation": "Induction over any disabled-clock block: pending/unaged bits accumulate any sampled rising edge; previous NMI is the final pin level; all other physical CPU state is preserved. This proves the block summary without bounding its duration.",
        "limits": "Disabled transitions only. Enabled instruction semantics, reset/warm restart, parent timing and universal Original whole-game equivalence remain separate obligations.",
        "universal_cpu_equivalence": False,
        "universal_game_equivalence": False,
    }
    write_json(directory / "result.json", report)
    if not report["passed"]:
        raise ValueError("Disabled NMI summary failed; retained full evidence")
    return report


if __name__ == "__main__":
    qualify()
