"""Prove minimum accepted-request spacing in both actual generated parent schedulers."""

import re
import subprocess
from pathlib import Path

from tools.bbc import build, bus_binding_proof, video96
from tools.validation.common import ROOT, write_json


def qualify(*, sources=None, directory=None):
    supplied = sources is not None
    if not supplied and not build.generation_current():
        raise ValueError("Current parent elaboration required")
    directory = directory or build.BBC / "cpu-request-deadline-proof"
    directory.mkdir(exist_ok=True)
    sources = sources or [
        (1, build.BBC / "BbcMachine.v", "BbcMachine"),
        (3, video96.DIRECTORY / "BbcMachine96.v", "BbcMachine96"),
    ]
    if len(sources) != 2 or {(m, top) for m, _, top in sources} != {
        (1, "BbcMachine"),
        (3, "BbcMachine96"),
    }:
        raise ValueError("Expected both declared machine clock variants")
    files = [
        Path(__file__),
        Path(bus_binding_proof.__file__),
        ROOT / "rtl/bbc/BbcMachine.scala",
        *[p for _, p, _ in sources],
    ]
    before = build.hashes(files)
    rows = []
    for multiplier, path, top in sources:
        source = path.read_text()
        parent = bus_binding_proof.module(source, top)
        cpu = bus_binding_proof.ports(parent, "BbcCpu", "cpu")
        if re.sub(r"\s+", "", cpu["enable"]) != "cpuEnable":
            raise ValueError("Different actual CPU acceptance port")
        width = 16 * multiplier - 1
        observation = f"""\nreg [{width - 1}:0] requestHistory=0;
initial assume(reset);
always @(posedge clk or posedge reset)
 if(reset)requestHistory<=0;
 else requestHistory<={{requestHistory[{width - 2}:0],cpuEnable}};
wire proofSpacing=!(cpuEnable && |requestHistory);
"""
        children = [
            name
            for name in re.findall(r"^\s+\w+\s+(\w+)\s*\(\s*$", parent, re.M)
            if name != "decode"
        ]
        cases = [
            ("positive", source),
            (
                "too-frequent-slots",
                source.replace("phase_1[3 : 0] == 4'b1111", "phase_1[2 : 0] == 3'b111"),
            ),
        ]
        results = []
        for label, text in cases:
            if label != "positive" and text == source:
                raise ValueError("Missing scheduler RTL fault anchor")
            current = bus_binding_proof.module(text, top)
            observed = text.replace(
                current, current.removesuffix("endmodule") + observation + "endmodule", 1
            )
            rtl = directory / f"{label}{multiplier}.v"
            rtl.write_text(observed)
            graph = directory / f"{label}{multiplier}.json"
            witness = directory / f"{label}{multiplier}-counterexample.json"
            command = (
                f"read_verilog -formal {rtl}; delete "
                + " ".join(f"{top}/c:{child}" for child in children)
                + f"; hierarchy -top {top}; proc; delete -output {top}/o:io_*; cutpoint {top}/w:cpu_address; expose {top}/w:proofSpacing; opt_clean -purge; flatten; async2sync; opt_clean; check -assert; write_json {graph}; sat -set-assumes -set-init-zero -tempinduct -initsteps {width + 1} -maxsteps {width + 5} -prove proofSpacing 1 -verify -timeout 120 -dump_json {witness}"
            )
            if label != "positive":
                command = (
                    command[: command.index("; sat ")]
                    + f"; sat -set-assumes -set-init-zero -seq {2 * (width + 1) + 4} -prove proofSpacing 1 -verify -dump_json {witness}"
                )
            log = directory / f"{label}{multiplier}.log"
            with log.open("w") as out:
                result = subprocess.run(
                    ["yosys", "-Q", "-T", "-p", command],
                    cwd=path.parent,
                    stdout=out,
                    stderr=subprocess.STDOUT,
                    timeout=600,
                )
            text = log.read_text()
            passed = (
                (result.returncode == 0 and "Induction step proven: SUCCESS!" in text)
                if label == "positive"
                else (
                    result.returncode != 0
                    and "SAT proof finished - model found: FAIL!" in text
                    and witness.is_file()
                )
            )
            if not passed:
                raise ValueError(f"Parent request-spacing proof failed: {label}/{multiplier}")
            results.append({"name": label, "accepted": passed})
        rows.append(
            {
                "clock_multiplier": multiplier,
                "minimum_master_edges_between_acceptances": 16 * multiplier,
                "checks": results,
            }
        )
    stable = before == build.hashes(files)
    report = {
        "passed": stable,
        "root_generation_bound": not supplied,
        "inputs": before,
        "inputs_unchanged": stable,
        "artifacts": build.hashes(
            [p for p in directory.iterdir() if p.is_file() and p.name != "result.json"]
        ),
        "clocks": rows,
        "tools": {"yosys": subprocess.check_output(["yosys", "-V"], text=True).strip()},
        "scope": "Actual generated parent CPU enable expressions and phase/divider state at both master rates. Arbitrary CPU addresses and subsequent reset histories. No assumptions beyond the initial reset; no CPU enable or clock cutpoints. All accepted requests are separated by at least 16/48 master edges, so a two-edge RAM response pipeline has time to complete before the next acceptance. Actual doubled slot-frequency faults must fail at both rates.",
        "limits": "Request-spacing and local response-deadline premise only. RAM/ROM contents, peripheral sampling, CPU semantics and Original cycle-phase equivalence require their separate contracts. Child outputs other than the address decoder are removed from this cone; no whole-game claim.",
        "universal_game_equivalence": False,
    }
    write_json(directory / "result.json", report)
    if not stable:
        raise ValueError("Parent scheduler sources changed")
    return report


if __name__ == "__main__":
    qualify()
