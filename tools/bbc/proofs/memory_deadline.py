"""Prove actual RAM response capture completes before the next CPU acceptance."""

import re
import subprocess
from pathlib import Path

from tools.bbc import build, bus_binding_proof, video96
from tools.validation.common import ROOT, write_json


def observed(source, top):
    parent = bus_binding_proof.module(source, top)
    ram = bus_binding_proof.module(source, "BbcRam")
    capture = re.findall(r"if\(([^()]*)\) begin\s*response <= data;", ram)
    if len(capture) != 1:
        raise ValueError("Changed actual RAM response-capture driver")
    header = ram.index(");")
    ram = (
        ram[:header]
        + ", output wire proofCapture\n"
        + ram[header : ram.rindex("endmodule")]
        + f"assign proofCapture={capture[0]};\nendmodule"
    )
    child = re.search(r"\bBbcRam\s+ram\s*\([\s\S]*?\n\s*\);", parent)
    if child is None:
        raise ValueError("Missing actual RAM child")
    replacement = child[0].removesuffix(");") + ", .proofCapture(proofRamCapture)\n);"
    parent = parent.replace(child[0], replacement, 1)
    addition = """
wire proofRamCapture;
reg responseOutstanding=0,previousRequest=0;
initial assume(reset);
always @(posedge clk or posedge reset)
 if(reset)begin responseOutstanding<=0;previousRequest<=0;end
 else begin
  if(ram_io_cpuEnable)responseOutstanding<=1;
  else if(proofRamCapture)responseOutstanding<=0;
  if(cpuEnable)previousRequest<=1;
 end
wire proofDeadline=(responseOutstanding==proofRamCapture) &&
 !(cpuEnable && previousRequest && memorySelect==0 && responseOutstanding);
"""
    original = bus_binding_proof.module(source, top)
    return source.replace(bus_binding_proof.module(source, "BbcRam"), ram, 1).replace(
        original, parent.removesuffix("endmodule") + addition + "endmodule", 1
    )


def qualify(*, sources=None, directory=None):
    supplied = sources is not None
    if not supplied and not build.generation_current():
        raise ValueError("Current parent elaboration required")
    directory = directory or build.BBC / "memory-response-deadline-proof"
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
        ROOT / "rtl/bbc/BbcRam.scala",
        ROOT / "rtl/bbc/BbcMachine.scala",
        *[p for _, p, _ in sources],
    ]
    before = build.hashes(files)
    clocks = []
    for multiplier, path, top in sources:
        source = path.read_text()
        children = [
            name
            for name in re.findall(
                r"^\s+\w+\s+(\w+)\s*\(\s*$", bus_binding_proof.module(source, top), re.M
            )
            if name not in {"decode", "ram"}
        ]
        cases = []
        for name in ("positive", "lost-response-capture"):
            text = source
            if name != "positive":
                anchor = "if(io_cpuEnable_regNext) begin\n        response <= data;"
                if text.count(anchor) != 1:
                    raise ValueError("Missing actual RAM capture fault anchor")
                text = text.replace(anchor, "if(1'b0) begin\n        response <= data;", 1)
            model = directory / f"{name}{multiplier}.v"
            model.write_text(observed(text, top))
            graph = directory / f"{name}{multiplier}.json"
            witness = directory / f"{name}{multiplier}-counterexample.json"
            command = (
                f"read_verilog -formal -D SYNTHESIS {model}; delete "
                + " ".join(f"{top}/c:{child}" for child in children)
                + f"; hierarchy -top {top}; proc; delete -output {top}/o:io_*; "
                + f"cutpoint {top}/w:cpu_address; expose {top}/w:proofDeadline; "
                + f"opt_clean -purge; flatten; async2sync; opt_clean; check -assert; write_json {graph}; "
            )
            if name == "positive":
                command += "sat -set-assumes -set-init-zero -tempinduct -initsteps 3 -maxsteps 12 -prove proofDeadline 1 -verify -timeout 90"
            else:
                command += f"sat -set-assumes -set-init-zero -seq {16 * multiplier * 2 + 8} -prove proofDeadline 1 -verify -dump_json {witness}"
            log = directory / f"{name}{multiplier}.log"
            with log.open("w") as out:
                result = subprocess.run(
                    ["yosys", "-Q", "-T", "-p", command],
                    cwd=path.parent,
                    stdout=out,
                    stderr=subprocess.STDOUT,
                    timeout=600,
                )
            output = log.read_text()
            accepted = (
                result.returncode == 0 and "Induction step proven: SUCCESS!" in output
                if name == "positive"
                else result.returncode != 0
                and "SAT proof finished - model found: FAIL!" in output
                and witness.is_file()
            )
            if not accepted:
                raise ValueError(f"RAM response deadline failed: {name}/{multiplier}")
            cases.append({"name": name, "accepted": accepted})
        clocks.append({"clock_multiplier": multiplier, "checks": cases})
    stable = before == build.hashes(files)
    write_json(
        directory / "result.json",
        {
            "passed": stable,
            "root_generation_bound": not supplied,
            "inputs": before,
            "inputs_unchanged": stable,
            "artifacts": build.hashes(
                p for p in directory.iterdir() if p.is_file() and p.name != "result.json"
            ),
            "clocks": clocks,
            "tools": {"yosys": subprocess.check_output(["yosys", "-V"], text=True).strip()},
            "scope": "Both actual parent CPU schedulers, registered memory selection, RAM request wiring and actual child response-capture guard. Every accepted RAM request has reached its holding-register capture edge before the next CPU acceptance that consumes RAM. Arbitrary CPU addresses and subsequent resets; only initial reset assumed. Actual lost response-capture mutations require initialized counterexamples at both rates.",
            "limits": "Capture deadline only. RAM contents and captured-byte correctness are supplied by the separate watched-word RAM theorem; this cone retains the exact capture guard, not the discarded data path. No independent reference phase, peripheral or whole-game equivalence claim.",
            "universal_game_equivalence": False,
        },
    )
    if not stable:
        raise ValueError("RAM deadline inputs changed")


if __name__ == "__main__":
    qualify()
