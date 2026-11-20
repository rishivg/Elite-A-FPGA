"""Prove NMI retention, vector selection and first-handler timing properties."""

import json
import re
import shutil
import subprocess
from pathlib import Path

from tools.validation.common import ROOT, digest, write_json

from . import build, cpu_instruction_proof, prepare, via_timer1_state_proof

PORTS = {
    "State": ("[5:0]", "state"),
    "Pending": ("", "NMI_edge"),
    "ResetFlag": ("", "res"),
    "PreviousNmi": ("", "NMI_1"),
    "Vector": ("", "bbc_nmi_vector"),
    "Polls": (
        "[5:0]",
        "{bbc_irq_sample,bbc_irq_second,bbc_irq_polled,bbc_nmi_sample,bbc_nmi_second,bbc_nmi_polled}",
    ),
    "VectorBus": ("[15:0]", "PC_temp"),
}
FAULTS = (
    (
        "old-nmi-poll",
        "if( state == BRK3 ) begin\n            bbc_nmi_sample <= 0;",
        "if( state == BRK3 && bbc_nmi_vector ) begin\n            bbc_nmi_sample <= 0;",
    ),
    (
        "late-vector-selection",
        "else if( CE && state == BRK1 )\n        bbc_nmi_vector <= (NMI_edge || (NMI && !NMI_1)) && !res;",
        "else if( CE && state == BRK2 )\n        bbc_nmi_vector <= (NMI_edge || (NMI && !NMI_1)) && !res;",
    ),
    (
        "late-nmi-ack",
        "else if( CE && state == BRK1 && !res )\n        NMI_edge <= 0;",
        "else if( CE && bbc_nmi_vector && state == BRK3 )\n        NMI_edge <= 0;",
    ),
)


def observed(core, wrapper, *, ports=None):
    """Add read-only ports to actual signals; change no DUT state or driver."""
    ports = PORTS if ports is None else ports
    for state, value in {
        "BRK1": 9,
        "BRK2": 10,
        "BRK3": 11,
        "JMP0": 22,
        "JMP1": 23,
        "DECODE": 12,
    }.items():
        if len(re.findall(rf"\b{state}\s*=\s*6'd{value}\b", core)) != 1:
            raise ValueError("Changed CPU protocol context binding: " + state)
    for name in (
        "NMI_edge",
        "res",
        "NMI_1",
        "bbc_nmi_vector",
        "bbc_irq_sample",
        "bbc_irq_second",
        "bbc_irq_polled",
        "bbc_nmi_sample",
        "bbc_nmi_second",
        "bbc_nmi_polled",
    ):
        if len(re.findall(r"\breg\s+" + name + r"\s*(?:=\s*0)?\s*;", core)) != 1:
            raise ValueError("Changed CPU protocol signal binding: " + name)
    if (
        len(re.findall(r"\breg\s+\[5:0\]\s+state\s*;", core)) != 1
        or len(re.findall(r"\breg\s+\[15:0\]\s+PC_temp\s*;", core)) != 1
    ):
        raise ValueError("Changed CPU state/vector width")
    if wrapper.count("wire step = enable && reset_dummy == 0;") != 1:
        raise ValueError("Changed actual CPU accepted-clock binding")
    for name in ("warm_pending", "warm_second"):
        declarations = [
            part.strip()
            for declaration in re.findall(r"\breg\s+([^;]+);", wrapper)
            for part in declaration.split(",")
        ]
        if (
            sum(
                re.fullmatch(name + r"\s*=\s*0", declaration) is not None
                for declaration in declarations
            )
            != 1
        ):
            raise ValueError("Changed actual CPU restart-control binding: " + name)
    header = core.index(");")
    core = core[:header] + "," + ",".join("BBC_PROOF_" + n for n in ports) + core[header:]
    header = core.index(");") + 2
    core = (
        core[:header]
        + "".join(
            "\noutput "
            + width
            + " BBC_PROOF_"
            + name
            + ";\nassign BBC_PROOF_"
            + name
            + "="
            + signal
            + ";"
            for name, (width, signal) in ports.items()
        )
        + core[header:]
    )
    header = wrapper.index(");")
    wrapper = (
        wrapper[:header]
        + ","
        + ",".join(
            "output wire " + width + " proof" + name
            for name, width in [
                *((n, w) for n, (w, _) in ports.items()),
                ("Accepted", ""),
                ("RestartControls", "[1:0]"),
            ]
        )
        + wrapper[header:]
    )
    connection = ".BBC_P(flags));"
    if wrapper.count(connection) != 1:
        raise ValueError("Changed actual wrapper/core observation connection")
    wrapper = wrapper.replace(
        connection,
        ".BBC_P(flags)," + ",".join(".BBC_PROOF_" + n + "(proof" + n + ")" for n in ports) + ");",
    )
    if wrapper.count("endmodule") != 1:
        raise ValueError("Changed actual wrapper module inventory")
    wrapper = wrapper.replace(
        "endmodule",
        "assign proofAccepted=step;\n"
        "assign proofRestartControls={warm_pending,warm_second};\nendmodule",
    )
    return core + wrapper


def supporting_evidence(path):
    report = cpu_instruction_proof.supporting_evidence(path)
    if (
        report.get("cases") != 96640
        or report.get("enabled_cycles_per_period") != 8697600
        or len(report.get("negative_controls", [])) != 3
        or {n.get("name") for n in report.get("negative_controls", [])}
        != {name for name, _, _ in FAULTS}
        or any(n.get("detected") is not True for n in report["negative_controls"])
        or report.get("universal_cpu_equivalence") is not False
        or report.get("universal_game_equivalence") is not False
    ):
        raise ValueError("Incomplete independent CPU interrupt collision evidence")
    return report


def qualify():
    prepare.main()
    directory = build.BBC / "cpu-interrupt-proof"
    directory.mkdir(exist_ok=True)
    core = build.BBC / "ArletCpuEnabled.v"
    wrapper = ROOT / "rtl/bbc/BbcCpu.v"
    contract = ROOT / "sim/formal/BbcCpuInterruptContract.v"
    support = build.BBC / "cpu-interrupt-matrix/result.json"
    evidence = supporting_evidence(support)
    dependencies = [
        Path(__file__),
        Path(prepare.__file__),
        Path(cpu_instruction_proof.__file__),
        Path(via_timer1_state_proof.__file__),
        ROOT / "tools/bbc/cpu_bus.py",
        core,
        wrapper,
        contract,
        support,
    ]
    before = build.hashes(dependencies)

    def prove(actual, name, *, induction):
        model = directory / (name + ".v")
        graph = directory / (name + ".json")
        model.write_text(observed(actual, wrapper.read_text()) + contract.read_text())
        query = (
            "-tempinduct -seq 3 -maxsteps 12 -prove equivalent 1 -verify"
            if induction
            else "-seq 20 -prove equivalent 1 -falsify -dump_vcd "
            + str(directory / (name + ".vcd"))
        )
        command = f"read_verilog -formal -sv {model}; prep -top CpuInterruptContract -flatten; memory_map; async2sync; opt_clean; write_json {graph}; sat -set-assumes -set-init-zero {query} -timeout 90"
        result = subprocess.run(
            ["yosys", "-Q", "-T", "-p", command], capture_output=True, text=True, timeout=120
        )
        (directory / (name + ".log")).write_text(result.stdout + result.stderr)
        return result

    actual = core.read_text()
    positive = prove(actual, "proof", induction=True)
    negatives = []
    for name, correct, wrong in FAULTS:
        if actual.count(correct) != 1:
            raise ValueError("Changed actual CPU interrupt fault target: " + name)
        result = prove(actual.replace(correct, wrong), name, induction=False)
        negatives.append(
            {
                "name": name,
                "detected": result.returncode == 0
                and "SAT proof finished - model found: FAIL!" in result.stdout
                and (directory / (name + ".vcd")).is_file(),
            }
        )
    via_timer1_state_proof.initial_reset_assumption(
        json.loads((directory / "proof.json").read_text()), "CpuInterruptContract"
    )
    if contract.read_text().count("assume(reset)") != 1:
        raise ValueError("Changed interrupt protocol initial-reset assumption")
    stable = before == build.hashes(dependencies) and evidence == supporting_evidence(support)
    report = {
        "passed": positive.returncode == 0
        and "Induction step proven: SUCCESS!" in positive.stdout
        and stable
        and all(n["detected"] for n in negatives),
        "inputs": before,
        "inputs_unchanged": stable,
        "negative_controls": negatives,
        "tools": {
            "yosys": subprocess.check_output(["yosys", "-V"], text=True).strip(),
            "yosys_sha256": digest(Path(shutil.which("yosys"))),
        },
        "artifacts": build.hashes(
            [
                *directory.glob("*.v"),
                *directory.glob("*.vcd"),
                *directory.glob("*.json"),
                *directory.glob("*.log"),
            ]
        ),
        "scope": "Temporal induction of selected interrupt protocol properties on the actual complete CPU/wrapper/ALU, observed without state injection or constraints. NMI edges remain pending until the vector-selection cutoff; selected vector and accepted address agree; IRQ/NMI polling cannot discard the first handler opcode. Pipeline predicates and inactivity of the two restart controls are jointly proved invariants with warm_reset tied inactive. Arbitrary subsequent reset, enable/stall, IRQ, NMI and read-data histories after the sole initial reset assumption. Independent all-opcode collision tests separately bind these semantics; all three previous-behavior faults have ordinary reachable bounded counterexamples.",
        "limits": "Specific universal protocol properties, not a proof of every CPU state, instruction semantic, undocumented opcode, original warm-reset policy or whole game equivalence. Uninitialized non-reset state uses the declared zero simulation profile. Parent clock/IRQ/NMI connections and original reference behavior are separate obligations.",
        "universal_cpu_equivalence": False,
        "universal_game_equivalence": False,
    }
    report["artifacts"].pop(str((directory / "result.json").relative_to(ROOT)), None)
    write_json(directory / "result.json", report)
    print(f"Actual CPU interrupt protocol induction: {report['passed']}", flush=True)
    if not report["passed"]:
        raise ValueError("CPU interrupt protocol proof failed; all models/witnesses retained")
    return report


if __name__ == "__main__":
    qualify()
