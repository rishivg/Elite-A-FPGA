"""Prove the exact accepted CPU transition after an arbitrary NMI wait block."""

import argparse
import json
import subprocess
from pathlib import Path

from tools.bbc import build, cpu_interrupt_proof
from tools.bbc.proofs import nmi_macro, restart_macro
from tools.validation.common import ROOT, write_json


def qualify(restart=False):
    p = build.BBC / (
        "cpu-restart-macro-transition-proof" if restart else "cpu-nmi-macro-transition-proof"
    )
    p.mkdir(exist_ok=True)
    q = p
    core = build.BBC / "ArletCpuEnabled.v"
    wrapper = ROOT / "rtl/bbc/BbcCpu.v"
    files = [
        Path(__file__),
        core,
        wrapper,
        ROOT / "third_party/6502/ALU.v",
        Path(cpu_interrupt_proof.__file__),
        Path(nmi_macro.__file__),
    ]
    if restart:
        files.append(Path(restart_macro.__file__))
    before = build.hashes(files)
    transform = restart_macro.transform if restart else nmi_macro.transform
    macro_core, macro_wrapper = transform(core.read_text(), wrapper.read_text())
    (q / "macro-core.v").write_text(macro_core)
    (q / "macro-wrapper.v").write_text(macro_wrapper)
    cpu_interrupt_proof.PORTS.update(
        BranchIrq=("", "bbc_branch_irq"), BranchNmi=("", "bbc_branch_nmi")
    )
    (p / "observed-actual.v").write_text(
        cpu_interrupt_proof.observed(
            (build.BBC / "ArletCpuEnabled.v").read_text(), (ROOT / "rtl/bbc/BbcCpu.v").read_text()
        )
    )
    (q / "observed-cpu.v").write_text(cpu_interrupt_proof.observed(macro_core, macro_wrapper))
    models = {}
    for label, src in [("actual", [p / "observed-actual.v"]), ("macro", [q / "observed-cpu.v"])]:
        raw = p / (label + ".json")
        cmd = (
            "read_verilog "
            + " ".join(map(str, src))
            + f"; hierarchy -top BbcCpu; flatten; proc; memory_map; opt; dffunmap; write_json {raw}"
        )
        with (p / (label + "-prepare.log")).open("w") as out:
            subprocess.run(
                ["yosys", "-Q", "-T", "-p", cmd],
                stdout=out,
                stderr=subprocess.STDOUT,
                cwd=ROOT,
                check=True,
                timeout=60,
            )
        models[label] = json.loads(raw.read_text())["modules"]["BbcCpu"]
    body = models["actual"]
    qbits = {
        bit
        for c in body["cells"].values()
        if c["type"] in ["$dff", "$adff"]
        for bit in c["connections"]["Q"]
    }
    assert len(qbits) == 168
    refs = []
    seen = set()
    for name, w in sorted(body["netnames"].items()):
        if w["hide_name"] or not w["bits"] or not all(bit in qbits for bit in w["bits"]):
            continue
        for i, bit in enumerate(w["bits"]):
            if bit not in seen:
                refs.append((name, i))
                seen.add(bit)
    assert len(refs) == 168
    cuts = {}
    indices = {}
    for label, body in models.items():
        state = [body["netnames"][n]["bits"][i] for n, i in refs]
        assert len(set(state)) == 168
        next_by_q = {}
        for name, c in list(body["cells"].items()):
            if c["type"] not in ["$dff", "$adff"]:
                assert not any(k in c["type"].lower() for k in ["ff", "latch", "mem"])
                continue
            assert (
                c["connections"]["CLK"] == body["ports"]["clk"]["bits"]
                and int(c["parameters"]["CLK_POLARITY"], 2) == 1
            )
            if c["type"] == "$adff":
                assert (
                    c["connections"]["ARST"] == body["ports"]["reset"]["bits"]
                    and int(c["parameters"]["ARST_POLARITY"], 2) == 1
                )
            for old, new in zip(c["connections"]["Q"], c["connections"]["D"]):
                next_by_q[old] = new
            del body["cells"][name]
        assert set(next_by_q) == set(state)
        for wire in body["netnames"].values():
            wire["attributes"].pop("init", None)
        body["ports"]["state_in"] = {"direction": "input", "bits": state}
        body["ports"]["next_out"] = {"direction": "output", "bits": [next_by_q[b] for b in state]}
        top = "NextActual" if label == "actual" else "NextMacro"
        body["attributes"]["top"] = "0"
        path = p / (label + "-cut.json")
        write_json(
            path,
            {
                "modules": {top: body},
                "creator": "Exact physical D/Q cut; all 168 state bits preserved",
            },
        )
        cuts[label] = path
        if label == "actual":
            for field in [
                "core.NMI_1",
                "core.NMI_edge",
                "core.bbc_nmi_unaged",
                "reset_dummy",
                *(["warm_pending"] if restart else []),
            ]:
                indices[field] = [state.index(b) for b in body["netnames"][field]["bits"]]
    bitexpr = [f"oldState[{i}]" for i in range(168)]
    bitexpr[indices["core.NMI_1"][0]] = "lastNmi"
    for name in ["core.NMI_edge", "core.bbc_nmi_unaged", *(["warm_pending"] if restart else [])]:
        i = indices[name][0]
        event = "offWarm" if name == "warm_pending" else "offEvent"
        bitexpr[i] = f"(oldState[{i}]|{event})"
    actual_state = "{" + ",".join(reversed(bitexpr)) + "}"
    dummy = "{" + ",".join(f"oldState[{i}]" for i in reversed(indices["reset_dummy"])) + "}"
    functional = [
        "address",
        "write_data",
        "write_enable",
        "instruction",
        "sync",
        "pc",
        "accumulator",
        "index_x",
        "index_y",
        "stack_pointer",
        "flags",
    ]
    wires = []
    actual_ports = []
    macro_ports = []
    comparisons = []
    for name in functional:
        width = len(models["actual"]["ports"][name]["bits"])
        assert width == len(models["macro"]["ports"][name]["bits"])
        wires.append(f"wire [{width - 1}:0] a_{name},b_{name};")
        actual_ports.append(f".{name}(a_{name})")
        macro_ports.append(f".{name}(b_{name})")
        comparisons.append(f"a_{name}==b_{name}")
    wire_text = "\n".join(wires)
    actual_text = ",".join(actual_ports)
    macro_text = ",".join(macro_ports)
    functional_equal = " && ".join(comparisons)
    warm_inputs = ",offWarm,freshWarm" if restart else ""
    warm_pin = "freshWarm" if restart else "1'b0"
    warm_port = ".offWarm(offWarm)," if restart else ""
    s = f"""module NmiMacroTransition(input [167:0] oldState,input nmi,lastNmi,offEvent,irq{warm_inputs},input [7:0] data,output equivalent);
    wire [167:0] a,b;
    {wire_text}
    NextActual original(.clk(1'b0),.reset(1'b0),.enable(1'b1),.warm_reset({warm_pin}),.nmi(nmi),.irq(irq),.read_data(data),.state_in({actual_state}),.next_out(a),{actual_text});
    NextMacro macro(.clk(1'b0),.reset(1'b0),.enable(1'b1),.warm_reset({warm_pin}),.nmi(nmi),.irq(irq),.read_data(data),{warm_port}.offEvt(offEvent),.riseEvt(nmi&&!lastNmi),.state_in(oldState),.next_out(b),{macro_text});
    assign equivalent={dummy}!=0 || (a==b && {functional_equal});
    endmodule
    """
    (p / "contract.v").write_text(s)
    cmd = f"read_json {cuts['actual']}; read_json {cuts['macro']}; read_verilog {p}/contract.v; hierarchy -top NmiMacroTransition; flatten; opt_clean; check -assert; sat -prove equivalent 1 -verify -show oldState -show nmi -show lastNmi -show offEvent -show equivalent -dump_json {p}/counterexample.json"
    variants = [("positive", q / "observed-cpu.v")]
    faults = [
        (
            "lost-wait-pending",
            "wire bbc_macro_pending = NMI_edge || BBC_OFF_EVT;",
            "wire bbc_macro_pending = NMI_edge;",
        ),
        (
            "lost-wait-unaged",
            "wire bbc_macro_unaged = bbc_nmi_unaged || BBC_OFF_EVT;",
            "wire bbc_macro_unaged = bbc_nmi_unaged;",
        ),
    ]
    if restart:
        faults.append(
            (
                "lost-wait-warm-seed",
                "wire macro_warm_pending = warm_pending || offWarm;",
                "wire macro_warm_pending = warm_pending;",
            )
        )
    for label, correct, wrong in faults:
        text = (q / "observed-cpu.v").read_text()
        if text.count(correct) != 1:
            raise ValueError("Changed virtual macro fault site")
        model = p / (label + ".v")
        model.write_text(text.replace(correct, wrong))
        variants.append((label, model))
    rows = []
    for label, model in variants:
        if label == "positive":
            command = cmd
        else:
            command = f"read_json {cuts['actual']}; read_verilog {model}; hierarchy -top BbcCpu; flatten BbcCpu; proc; memory_map; opt; dffunmap; write_json {p}/{label}-raw.json"
            with (p / (label + "-prepare.log")).open("w") as output:
                subprocess.run(
                    ["yosys", "-Q", "-T", "-p", command],
                    stdout=output,
                    stderr=subprocess.STDOUT,
                    cwd=ROOT,
                    check=True,
                    timeout=60,
                )
            body = json.loads((p / (label + "-raw.json")).read_text())["modules"]["BbcCpu"]
            state = [body["netnames"][n]["bits"][i] for n, i in refs]
            next_by_q = {}
            for name, c in list(body["cells"].items()):
                if c["type"] not in ["$dff", "$adff"]:
                    assert not any(k in c["type"].lower() for k in ["ff", "latch", "mem"])
                    continue
                for old, new in zip(c["connections"]["Q"], c["connections"]["D"]):
                    next_by_q[old] = new
                del body["cells"][name]
            assert set(next_by_q) == set(state) and len(set(state)) == 168
            for wire in body["netnames"].values():
                wire["attributes"].pop("init", None)
            body["ports"]["state_in"] = {"direction": "input", "bits": state}
            body["ports"]["next_out"] = {
                "direction": "output",
                "bits": [next_by_q[b] for b in state],
            }
            body["attributes"]["top"] = "0"
            cut = p / (label + "-cut.json")
            write_json(cut, {"modules": {"NextMacro": body}})
            command = cmd.replace(str(cuts["macro"]), str(cut)).replace(
                str(p / "counterexample.json"), str(p / (label + "-counterexample.json"))
            )
        log = p / (label + "-proof.log")
        with log.open("w") as output:
            run = subprocess.run(
                ["yosys", "-Q", "-T", "-p", command],
                stdout=output,
                stderr=subprocess.STDOUT,
                cwd=ROOT,
                timeout=120,
            )
        text = log.read_text()
        accepted = (
            (run.returncode == 0 and "no model found: SUCCESS!" in text)
            if label == "positive"
            else (
                run.returncode != 0
                and "proof did fail" in text
                and (p / (label + "-counterexample.json")).is_file()
            )
        )
        rows.append({"name": label, "accepted": accepted})
        print(rows[-1], flush=True)
    # Before live NMI begins, the same model executes every reset dummy and
    # initialization edge. This lemma covers all reset_dummy values.
    initial = s.replace(".nmi(nmi)", ".nmi(1'b0)")
    if restart:
        initial = initial.replace(".offWarm(offWarm)", ".offWarm(1'b0)").replace(
            ".warm_reset(freshWarm)", ".warm_reset(1'b0)"
        )
    initial = initial.replace(".offEvt(offEvent)", ".offEvt(1'b0)")
    initial = initial.replace(".riseEvt(nmi&&!lastNmi)", ".riseEvt(1'b0)")
    initial = initial.replace(".state_in(" + actual_state + ")", ".state_in(oldState)")
    initial = initial.replace(
        "assign equivalent=" + dummy + "!=0 ||",
        f"assign equivalent=oldState[{indices['core.NMI_1'][0]}]!=0 ||",
    )
    initial_path = p / "initialization.v"
    initial_path.write_text(initial)
    initial_command = cmd.replace(str(p / "contract.v"), str(initial_path)).replace(
        str(p / "counterexample.json"), str(p / "initialization-counterexample.json")
    )
    with (p / "initialization-proof.log").open("w") as output:
        result = subprocess.run(
            ["yosys", "-Q", "-T", "-p", initial_command],
            stdout=output,
            stderr=subprocess.STDOUT,
            cwd=ROOT,
            timeout=120,
        )
    accepted = (
        result.returncode == 0
        and "no model found: SUCCESS!" in (p / "initialization-proof.log").read_text()
    )
    rows.append({"name": "inactive-nmi-initialization", "accepted": accepted})
    print(rows[-1], flush=True)
    stable = before == build.hashes(files)
    report = {
        "passed": stable and all(row["accepted"] for row in rows),
        "inputs": before,
        "inputs_unchanged": stable,
        "artifacts": build.hashes(
            [
                path
                for path in p.iterdir()
                if path.is_file() and path.name not in ["result.json", "started.json"]
            ]
        ),
        "register_bits": 168,
        "excluded_register_bits": 0,
        "functional_outputs": functional,
        "inactive_nmi_initialization_all_dummy_phases": True,
        "arbitrary_warm_request": restart,
        "proofs": rows,
        "tools": {"yosys": subprocess.check_output(["yosys", "-V"], text=True).strip()},
        "scope": (
            "Includes arbitrary current warm requests and wait-block request retention. "
            if restart
            else ""
        )
        + "For arbitrary physical state with reset_dummy zero, an arbitrary disabled NMI block followed by one accepted edge has exactly the same 168 physical next-state bits and bus/register outputs as the virtual accepted-edge CPU. Reset input inactive; warm-reset input inactive only in the NMI-only profile. The real block seeds retained pending/unaged bits and its final pin sample; the virtual model receives a wait event and the fresh accepted rise. Other state, IRQ and read-data are unrestricted. Formal D/Q cuts are verification inputs, not simulation state injection.",
        "limits": "Completed cold-reset dummy phase only. Disabled-block summary, instruction semantics, reset/warm restart and parent/device/video composition are separate obligations.",
        "universal_cpu_equivalence": False,
        "universal_game_equivalence": False,
    }
    write_json(p / "result.json", report)
    if not report["passed"]:
        raise ValueError("NMI macro transition proof or actual virtual-model fault check failed")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--restart", action="store_true", help="Include free warm requests")
    qualify(restart=parser.parse_args().restart)
