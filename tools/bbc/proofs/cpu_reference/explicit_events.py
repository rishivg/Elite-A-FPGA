"""Bind the event-input CPU to the complete level-input CPU transition theorems."""

import json
import shutil
import subprocess
from pathlib import Path

from tools.bbc import cpu_events, cycle_reference
from tools.bbc.proofs import memory_composition
from tools.bbc.proofs.cpu_reference import callback_order, nmi_sampling, prepared
from tools.bbc.proofs.video_cursor import hashes
from tools.validation.common import ROOT, write_json

DIRECTORY = ROOT / "build/bbc/cpu-explicit-events"
MACRO = ROOT / "build/bbc/cpu-nmi-macro-transition-proof"
IDLE = ROOT / "build/bbc/cpu-nmi-idle-summary"
FUNCTIONAL = (
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
)


def cut(graph, legacy, original):
    body = graph["modules"]["BbcCpuEvents"]
    if any(
        cell["type"] in {"$assume", "$check", "$anyseq", "$anyconst"}
        for cell in body["cells"].values()
    ):
        raise ValueError("Restricted event CPU state domain")
    state, next_by_q = [], {}
    for name, cell in list(body["cells"].items()):
        if cell["type"] not in {"$dff", "$adff"}:
            if any(token in cell["type"].lower() for token in ("ff", "latch", "mem")):
                raise ValueError("Unknown event CPU storage")
            continue
        ports = cell["connections"]
        if (
            ports["CLK"] != body["ports"]["clk"]["bits"]
            or int(cell["parameters"]["CLK_POLARITY"], 2) != 1
        ):
            raise ValueError("Changed event CPU clock")
        if cell["type"] == "$adff" and (
            ports["ARST"] != body["ports"]["reset"]["bits"]
            or int(cell["parameters"]["ARST_POLARITY"], 2) != 1
        ):
            raise ValueError("Changed event CPU reset")
        for q, d in zip(ports["Q"], ports["D"], strict=True):
            if q in next_by_q:
                raise ValueError("Duplicate event CPU state")
            next_by_q[q] = d
        del body["cells"][name]
    old = legacy["modules"]["NextActual"]
    old_state = old["ports"]["state_in"]["bits"]
    mapping = {}
    for name, wire in old["netnames"].items():
        if (
            wire.get("hide_name")
            or not any(q in old_state for q in wire["bits"])
            or name not in body["netnames"]
        ):
            continue
        target = body["netnames"][name]["bits"]
        if len(wire["bits"]) != len(target):
            raise ValueError("Changed event CPU field width: " + name)
        for before, after in zip(wire["bits"], target, strict=True):
            if before not in old_state:
                continue
            if after not in next_by_q or (before in mapping and mapping[before] != after):
                raise ValueError("Changed event CPU state alias")
            mapping[before] = after
    state = [mapping[q] for q in old_state]
    if len(state) != 168 or len(set(state)) != 168 or set(state) != set(next_by_q):
        raise ValueError("Incomplete 168-bit event CPU correspondence")

    def initials(module):
        values = {}
        for wire in module["netnames"].values():
            value = wire.get("attributes", {}).get("init")
            if value is None:
                continue
            if len(value) != len(wire["bits"]):
                raise ValueError("Invalid CPU initialization width")
            for bit, initial in zip(wire["bits"], reversed(value), strict=True):
                if bit in values and values[bit] != initial:
                    raise ValueError("Conflicting CPU initialization aliases")
                values[bit] = initial
        return values

    before_init, after_init = initials(original["modules"]["BbcCpu"]), initials(body)
    if any(
        before_init.get(old) != after_init.get(new)
        for old, new in zip(old_state, state, strict=True)
    ):
        raise ValueError("Changed CPU event initialization")
    for wire in body["netnames"].values():
        wire["attributes"].pop("init", None)
    body["ports"]["state_in"] = {"direction": "input", "bits": state}
    body["ports"]["next_out"] = {"direction": "output", "bits": [next_by_q[q] for q in state]}
    body["attributes"]["top"] = "0"
    return {"modules": {"NextEvents": body}}


def contract(legacy, mode):
    body = legacy["modules"]["NextActual"]
    bits = body["ports"]["state_in"]["bits"]
    indices = {
        name: bits.index(body["netnames"]["core." + name]["bits"][0])
        for name in ("NMI_1", "NMI_edge", "bbc_nmi_unaged")
    }
    declarations, left, right, equal = [], [], [], []
    for name in FUNCTIONAL:
        width = len(body["ports"][name]["bits"])
        declarations.append(f"wire[{width - 1}:0] a_{name},b_{name};")
        left.append(f".{name}(a_{name})")
        right.append(f".{name}(b_{name})")
        equal.append(f"a_{name}==b_{name}")
    header = "module EventCpuContract(input[167:0] state,input enable,reset,warm,nmi,event,irq,input[7:0]data,output equivalent);\n"
    actual = (
        "NextEvents actual(.state_in(state),.next_out(nextA),.clk(1'b0),.reset(reset),.warm_reset(warm),.enable(enable),.nmi(nmi),.nmi_event(event),.irq(irq),.read_data(data),"
        + ",".join(left)
        + ");\n"
    )
    if mode == "disabled":
        expected = [f"state[{i}]" for i in range(168)]
        expected[indices["NMI_1"]] = "nmi"
        for name in ("NMI_edge", "bbc_nmi_unaged"):
            i = indices[name]
            expected[i] = f"(state[{i}] || rise)"
        relation = (
            "assign equivalent=enable || reset || warm || nextA=={"
            + ",".join(reversed(expected))
            + "};\n"
        )
        reference = ""
    else:
        module = "NextMacro" if mode == "accepted" else "NextActual"
        extras = ".offEvt(1'b0),.riseEvt(rise)," if mode == "accepted" else ""
        reference = (
            module
            + " reference(.state_in(state),.next_out(nextB),.clk(1'b0),.reset(reset),.warm_reset(warm),.enable(enable),.nmi(nmi),.irq(irq),.read_data(data),"
            + extras
            + ",".join(right)
            + ");\n"
        )
        domain = "1'b0" if mode == "accepted" else "event"
        relation = (
            f"assign equivalent=({domain}) || (nextA==nextB && " + " && ".join(equal) + ");\n"
        )
    return (
        header
        + "wire[167:0] nextA,nextB;\n"
        + f"wire rise=event || (nmi && !state[{indices['NMI_1']}]);\n"
        + "\n".join(declarations)
        + "\n"
        + actual
        + reference
        + relation
        + "endmodule\n"
    )


def native_outcome(trial, name):
    if name == "positive":
        return (
            trial.returncode == 0
            and not trial.stderr
            and trial.stdout == "CPU explicit events: 130 cold histories; 0 differences\n"
        )
    expected = {"lost-accepted": "1 phase 0", "lost-held": "16 phase 1"}[name]
    return (
        trial.returncode == 1
        and not trial.stderr
        and trial.stdout == f"CPU_EVENT_MISMATCH period {expected} withdraw 0 handlers 1/2\n"
    )


def native():
    checks, artifacts = [], []
    driver = ROOT / "sim/native/bbc_cpu_explicit_events_test.cpp"
    for name, expression in (
        ("positive", "nmi_event"),
        ("lost-accepted", "nmi_event && !enable"),
        ("lost-held", "nmi_event && enable"),
    ):
        case = DIRECTORY / ("native-" + name)
        case.mkdir(exist_ok=True)
        model = case / "actual.v"
        model.write_text(
            cpu_events.CORE.read_text()
            + "\n"
            + cpu_events.WRAPPER.read_text().replace(
                ".BBC_NMI_EVENT(nmi_event)", ".BBC_NMI_EVENT(" + expression + ")"
            )
        )
        binary = callback_order.compile_native(
            model,
            case,
            "BbcCpuEvents",
            driver,
            [ROOT / "build/bbc/reference-cycle/m6502.h", ROOT / "tools/bbc/reference/nmi.h"],
        )
        trial = subprocess.run([str(binary)], capture_output=True, text=True, timeout=60)
        log = case / "run.log"
        log.write_text(trial.stdout + trial.stderr)
        accepted = native_outcome(trial, name)
        print(f"Executing explicit events {name}: " + ("PASS" if accepted else "FAIL"), flush=True)
        if not accepted:
            raise ValueError("Explicit event CPU firmware differs: " + str(log))
        checks.append({"name": "native-" + name, "accepted": accepted})
        artifacts.extend([model, binary, log, case / "build.json", case / "build.log"])
    return checks, artifacts


def qualify():
    cpu_events.audit()
    if not cycle_reference.current():
        raise ValueError("Independent native CPU qualification is stale")
    parents = [MACRO / "result.json", IDLE / "result.json", nmi_sampling.DIRECTORY / "result.json"]
    for path in parents:
        memory_composition.sealed(path)
    DIRECTORY.mkdir(parents=True, exist_ok=True)
    legacy_path, macro_path = MACRO / "actual-cut.json", MACRO / "macro-cut.json"
    legacy = json.loads(legacy_path.read_text())
    original_path = MACRO / "actual.json"
    original = json.loads(original_path.read_text())
    files = [
        Path(__file__),
        *cpu_events.files(),
        legacy_path,
        original_path,
        macro_path,
        *parents,
        Path(prepared.__file__),
        Path(memory_composition.__file__),
        Path(callback_order.__file__),
        Path(cycle_reference.__file__),
        cycle_reference.RECEIPT,
        ROOT / "build/bbc/reference-cycle/m6502.h",
        ROOT / "tools/bbc/reference/nmi.h",
        ROOT / "sim/native/bbc_cpu_explicit_events_test.cpp",
        ROOT / "tools/bbc/proofs/video_cursor.py",
        ROOT / "tools/validation/common.py",
        Path(shutil.which("yosys")),
        Path(shutil.which("verilator")),
        Path(shutil.which("c++")),
    ]
    before = hashes(files)
    artifacts, checks = [], []
    variants = [
        ("inactive", "inactive", cpu_events.WRAPPER.read_text()),
        ("disabled", "disabled", cpu_events.WRAPPER.read_text()),
        ("accepted", "accepted", cpu_events.WRAPPER.read_text()),
    ]
    for name, mode, expression in (
        ("lost-event", "accepted", "1'b0"),
        ("lost-accepted", "accepted", "nmi_event && !enable"),
        ("lost-held", "disabled", "nmi_event && enable"),
        ("level-as-event", "accepted", "nmi"),
    ):
        variants.append(
            (
                name,
                mode,
                cpu_events.WRAPPER.read_text().replace(
                    ".BBC_NMI_EVENT(nmi_event)", ".BBC_NMI_EVENT(" + expression + ")"
                ),
            )
        )
    for name, mode, wrapper in variants:
        case = DIRECTORY / name
        case.mkdir(exist_ok=True)
        model, raw, symbolic, harness, log, witness = [
            case / n
            for n in ("actual.v", "raw.json", "cut.json", "contract.v", "proof.log", "witness.json")
        ]
        model.write_text(cpu_events.CORE.read_text() + "\n" + wrapper)
        prepared.prepare(
            case,
            f"read_verilog {model}; hierarchy -top BbcCpuEvents; flatten; proc; memory_map; opt; dffunmap; write_json {raw}",
            [model],
            [raw],
        )
        write_json(symbolic, cut(json.loads(raw.read_text()), legacy, original))
        harness.write_text(contract(legacy, mode))
        positive = name in {"inactive", "disabled", "accepted"}
        witness.unlink(missing_ok=True)
        with log.open("w") as output:
            result = subprocess.run(
                [
                    "yosys",
                    "-Q",
                    "-T",
                    "-p",
                    f"read_json {legacy_path} {macro_path} {symbolic}; read_verilog {harness}; prep -top EventCpuContract -flatten; opt; check -assert; sat -prove equivalent 1 -verify -timeout 120 -show-inputs -show equivalent -dump_json {witness}",
                ],
                stdout=output,
                stderr=subprocess.STDOUT,
                timeout=180,
            )
        text = log.read_text()
        accepted = (
            result.returncode == 0 and "SUCCESS!" in text
            if positive
            else result.returncode != 0
            and "proof did fail" in text
            and witness.is_file()
            and any(
                s["name"] == "equivalent" and "0" in s.get("wave", "")
                for s in json.loads(witness.read_text())["signal"]
            )
        )
        checks.append({"name": name, "accepted": accepted})
        print(f"Explicit event CPU {name}: " + ("PASS" if accepted else "FAIL"), flush=True)
        if not accepted:
            raise ValueError("Explicit CPU event relation failed: " + str(log))
        artifacts.extend(
            [
                model,
                raw,
                symbolic,
                harness,
                log,
                case / "prepared.json",
                case / "prepare.log",
                *([witness] if witness.is_file() else []),
            ]
        )
    more_checks, more_artifacts = native()
    checks.extend(more_checks)
    artifacts.extend(more_artifacts)
    for path in parents:
        memory_composition.sealed(path)
    stable = before == hashes(files)
    write_json(
        DIRECTORY / "result.json",
        {
            "passed": stable,
            "inputs_unchanged": stable,
            "inputs": before,
            "artifacts": hashes(artifacts),
            "checks": checks,
            "state_bits": 168,
            "scope": "All-state event-input CPU refinement with identical initialization: inactive explicit events preserve the complete level-input transition, including reset and warm controls. Every disabled transition accumulates raw rises OR explicit events in exactly the two retained bits and samples the physical pin; all other 165 bits hold. With arbitrary enable/reset/warm/dummy controls, the complete transition matches the sealed legacy macro with zero intervening event and current event equal to raw rise OR explicit event, on all 168 state bits and every functional output. Four formal mutations must fail. Independent native firmware compares two NMI handlers in 130 cold histories covering every physical phase at enable periods 1/16/48, including same-edge withdrawal; two native event-gating faults must lose exactly the second handler. No new CPU state or reference state injection.",
            "limits": "Source/device event generation and parent event gating require their own contracts. Cold official-opcode history composition remains conditional on matching response bytes, IRQ levels and event windows; arbitrary warm resets, actual opcode eligibility and whole-game/board equivalence are separate.",
            "universal_game_equivalence": False,
        },
    )
    if not stable:
        raise ValueError("CPU event proof inputs changed")


if __name__ == "__main__":
    qualify()
