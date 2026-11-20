"""Prove the production wrapper's warm-request and dummy-read protocol."""

import subprocess
from pathlib import Path

from tools.bbc import build
from tools.validation.common import ROOT, write_json


def observed(source):
    """Expose wrapper connections without changing their logic or state."""
    if source.count("module BbcCpu(") != 1 or source.count("endmodule") != 1:
        raise ValueError("Changed CPU wrapper module inventory")
    return source.replace(
        "module BbcCpu(",
        """module BbcCpu(
 output wire proofWarmBegin,proofWarmSecond,
 output wire proofCoreSync,proofCoreWrite,proofCoreInstruction,
 output wire [15:0] proofCoreAddress,proofCorePc,""",
    ).replace(
        "endmodule",
        """assign proofWarmBegin=warm_accept;
assign proofWarmSecond=warm_second;
assign proofCoreSync=core_sync;
assign proofCoreWrite=core_write;
assign proofCoreInstruction=core_instruction;
assign proofCoreAddress=core_address;
assign proofCorePc=core_pc;
endmodule""",
    )


def qualify():
    directory = build.BBC / "cpu-restart-control-proof"
    directory.mkdir(exist_ok=True)
    wrapper = ROOT / "rtl/bbc/BbcCpu.v"
    interface = ROOT / "sim/formal/BbcCpuCoreInterface.v"
    contract = ROOT / "sim/formal/BbcCpuRestartControlContract.v"
    inputs = [Path(__file__), wrapper, interface, contract]
    before = build.hashes(inputs)
    text = observed(wrapper.read_text())
    faults = [
        (
            "lost-disabled-request",
            "if (warm_reset) warm_pending <= 1;",
            "if (enable && warm_reset) warm_pending <= 1;",
        ),
        (
            "changing-second-address",
            "warm_second ? warm_address : core_address",
            "warm_second ? core_address : core_address",
        ),
    ]
    variants = [("positive", text)]
    for name, correct, wrong in faults:
        if text.count(correct) != 1:
            raise ValueError("Changed wrapper fault site: " + name)
        variants.append((name, text.replace(correct, wrong)))
    rows, artifacts = [], []
    for name, source in variants:
        model = directory / (name + ".v")
        model.write_text(source)
        graph = directory / (name + ".json")
        wave = directory / (name + "-counterexample.json")
        if wave.exists():
            wave.unlink()
        command = (
            f"read_verilog -formal {model} {interface} {contract}; "
            "prep -top BbcCpuRestartControlContract -flatten; check -assert; "
            f"opt; async2sync; opt_clean; write_json {graph}; "
            "sat -set-assumes -set-init-zero "
            + ("-tempinduct -seq 3 -maxsteps 12 " if name == "positive" else "-seq 10 ")
            + "-prove equivalent 1 -verify -timeout 60 "
            + "-show reset -show enable -show request -show address -show expectedAddress "
            + f"-show queued -show phase -dump_json {wave}"
        )
        script = directory / (name + ".ys")
        script.write_text(command + "\n")
        log = directory / (name + ".log")
        with log.open("w") as output:
            result = subprocess.run(
                ["yosys", "-Q", "-T", "-s", str(script)],
                stdout=output,
                stderr=subprocess.STDOUT,
                cwd=ROOT,
                timeout=120,
            )
        output = log.read_text()
        accepted = (
            result.returncode == 0 and "Induction step proven: SUCCESS!" in output
            if name == "positive"
            else result.returncode != 0 and "proof did fail" in output and wave.is_file()
        )
        rows.append({"name": name, "accepted": accepted})
        artifacts.extend([model, script, graph, log])
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
        "tools": {"yosys": subprocess.check_output(["yosys", "-V"], text=True).strip()},
        "scope": "Production wrapper cold dummy-read count, warm request retention, first/second dummy phases, retained restart address and bus/SYNC outputs. Only initial reset is assumed. Subsequent reset, enable, warm request and core interface outputs are unrestricted. Read-only observation changes no wrapper logic or state.",
        "limits": "The core interface is an explicit arbitrary cutpoint. This proves wrapper protocol, not core instruction/reset semantics, reference warm-reset policy, parent timing or full game equivalence. Zero simulation power-on profile.",
        "universal_cpu_equivalence": False,
        "universal_game_equivalence": False,
    }
    write_json(directory / "result.json", report)
    if not report["passed"]:
        raise ValueError("CPU restart wrapper proof failed; complete evidence retained")
    return report


if __name__ == "__main__":
    qualify()
