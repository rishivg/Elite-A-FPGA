"""Prove all disabled CPU state updates with free NMI and restart requests."""

import json
import subprocess
from pathlib import Path

from tools.bbc import build
from tools.bbc.proofs import nmi_idle_summary
from tools.validation.common import ROOT, write_json


def qualify():
    directory = build.BBC / "cpu-restart-idle-summary"
    directory.mkdir(exist_ok=True)
    core = build.BBC / "ArletCpuEnabled.v"
    wrapper = ROOT / "rtl/bbc/BbcCpu.v"
    inputs = [Path(__file__), Path(nmi_idle_summary.__file__), core, wrapper]
    before = build.hashes(inputs)
    variants = [("positive", core, wrapper)]
    mutations = [
        (
            "lost-wait-restart",
            wrapper,
            "if (warm_reset) warm_pending <= 1;",
            "if (enable && warm_reset) warm_pending <= 1;",
        ),
        ("gated-nmi-sample", core, "NMI_1 <= NMI;", "if (CE) NMI_1 <= NMI;"),
    ]
    artifacts = []
    for name, path, correct, wrong in mutations:
        source = path.read_text()
        if source.count(correct) != 1:
            raise ValueError("Changed actual wait-event mutation site")
        model = directory / (name + ".v")
        model.write_text(source.replace(correct, wrong))
        artifacts.append(model)
        variants.append(
            (name, model if path == core else core, model if path == wrapper else wrapper)
        )
    rows = []
    for name, model, wrap in variants:
        case = directory / name
        case.mkdir(exist_ok=True)
        symbolic = nmi_idle_summary.prepare_graph(model, wrap, case)
        graph = json.loads(symbolic.read_text())
        body = graph["modules"]["BbcCpu"]
        state = body["ports"]["state_in"]["bits"]
        queued = body["netnames"]["warm_pending"]["bits"]
        if len(queued) != 1 or queued[0] not in state:
            raise ValueError("Missing actual queued restart state")
        fresh = (
            max(
                bit
                for net in body["netnames"].values()
                for bit in net["bits"]
                if isinstance(bit, int)
            )
            + 1
        )
        # The existing summary has already allocated comparison gates above
        # the original netnames, so inventory every cell connection too.
        fresh = max(
            fresh,
            1
            + max(
                bit
                for cell in body["cells"].values()
                for bits in cell["connections"].values()
                for bit in bits
                if isinstance(bit, int)
            ),
        )
        body["cells"]["summary_restart"] = {
            "hide_name": 0,
            "type": "$or",
            "attributes": {},
            "parameters": {
                "A_WIDTH": format(1, "032b"),
                "B_WIDTH": format(1, "032b"),
                "Y_WIDTH": format(1, "032b"),
                "A_SIGNED": "0" * 32,
                "B_SIGNED": "0" * 32,
            },
            "port_directions": {"A": "input", "B": "input", "Y": "output"},
            "connections": {"A": queued, "B": body["ports"]["warm_reset"]["bits"], "Y": [fresh]},
        }
        body["cells"]["summary_compare"]["connections"]["A"][state.index(queued[0])] = fresh
        write_json(symbolic, graph)
        wave = case / "counterexample.json"
        if wave.exists():
            wave.unlink()
        command = f"read_json {symbolic}; hierarchy -top BbcCpu; opt_clean; check -assert; sat -set enable 0 -set reset 0 -prove summary_holds 1 -verify -show state_in -show nmi -show warm_reset -show summary_holds -dump_json {wave}"
        log = case / "proof.log"
        with log.open("w") as output:
            result = subprocess.run(
                ["yosys", "-Q", "-T", "-p", command],
                stdout=output,
                stderr=subprocess.STDOUT,
                cwd=ROOT,
                timeout=60,
            )
        text = log.read_text()
        accepted = (
            result.returncode == 0 and "no model found: SUCCESS!" in text
            if name == "positive"
            else result.returncode != 0 and "proof did fail" in text and wave.is_file()
        )
        rows.append({"name": name, "accepted": accepted})
        artifacts.extend([case / "registers.json", case / "prepare.log", symbolic, log])
        if wave.is_file():
            artifacts.append(wave)
        print(rows[-1], flush=True)
    stable = before == build.hashes(inputs)
    report = {
        "passed": stable and all(row["accepted"] for row in rows),
        "inputs": before,
        "inputs_unchanged": stable,
        "artifacts": build.hashes(artifacts),
        "proofs": rows,
        "register_bits": 168,
        "excluded_register_bits": 0,
        "tools": {"yosys": subprocess.check_output(["yosys", "-V"], text=True).strip()},
        "scope": "All 168 actual physical CPU bits on a disabled edge, reset inactive, arbitrary NMI, warm request, IRQ, read data and state. Previous NMI becomes the pin; pending/unaged NMI accumulate a rise; queued warm restart becomes its old value OR request. Every other 164 bits hold. Formal D/Q cuts are verification inputs, not simulation state injection.",
        "derivation": "Induction over any disabled block: retain any physical NMI rise and any warm request independently, keep the final NMI sample and all other state.",
        "limits": "Disabled transitions only. Enabled instruction/reset semantics and parent/device/video composition remain separate obligations.",
        "universal_cpu_equivalence": False,
        "universal_game_equivalence": False,
    }
    write_json(directory / "result.json", report)
    if not report["passed"]:
        raise ValueError("Physical NMI/restart wait summary failed")
    return report


if __name__ == "__main__":
    qualify()
