"""Check actual parent IRQ/NMI remain inactive during a read-only startup prefix."""

import re
import subprocess
from pathlib import Path

from tools.bbc import build, bus_binding_proof, video96
from tools.validation.common import ROOT, write_json


def observed(source, top):
    parent = bus_binding_proof.module(source, top)
    cpu = bus_binding_proof.ports(parent, "BbcCpu", "cpu")
    fdc = bus_binding_proof.module(source, "Intel8271")
    for anchor in ("localparam Phase_Idle = 4'd0;", "nmiLevel", "writeCrcStage", "indexCallback"):
        if anchor not in fdc:
            raise ValueError("Changed disc startup observation")
    header = fdc.index(");")
    observed_fdc = (
        fdc[:header]
        + ", output wire proofStartupState\n"
        + fdc[header : fdc.rindex("endmodule")]
        + "assign proofStartupState=phase_1==Phase_Idle && mechanicalPhase==Mechanical_Idle && !dispatchPending && !nmiLevel && writeCrcStage==0 && indexCallback==0 && driveOut==0;\nendmodule"
    )
    instance = re.search(r"\bIntel8271\s+\w+\s*\([\s\S]*?\n\s*\);", parent)
    parent = parent.replace(
        instance[0],
        instance[0].removesuffix(");") + ", .proofStartupState(proofDiscStartup)\n);",
        1,
    )
    children = re.findall(r"^\s+(\w+)\s+(\w+)\s*\(\s*$", parent, re.M)
    removed = []
    cuts = {"cpu_address", "cpu_write_data"}
    for kind, instance in children:
        if kind in {"BbcAddressDecode", "Via6522", "BbcSerial", "Intel8271"}:
            continue
        removed.append(instance)
        if kind in {"BbcCpu", "BbcCpuEvents"}:
            continue
        ports = bus_binding_proof.ports(parent, kind, instance)
        module = bus_binding_proof.module(source, kind)
        outputs = re.findall(
            r"output\s+(?:wire|reg)\s*(?:\[[^\]]+\]\s*)?(\w+)",
            module[: module.index(");")],
        )
        for output in outputs:
            expression = ports[output]
            name = re.sub(r"\[[^\]]*\]", "", expression).strip()
            if not re.fullmatch(r"\w+", name):
                raise ValueError("Changed abstracted child output expression")
            cuts.add(name)
    addition = f"""
// Read-only prefix fixture replaces the removed CPU output driver, not any DUT state.
assign cpu_write_enable=1'b0;
initial assume(reset);
wire proofQuiet=!({cpu["irq"]} || {cpu["nmi"]} || {cpu.get("nmi_event", "1'b0")});
wire proofDiscStartup;
wire proofContract=proofQuiet && proofDiscStartup;
"""
    return (
        source.replace(fdc, observed_fdc, 1).replace(
            bus_binding_proof.module(source, top),
            parent.removesuffix("endmodule") + addition + "endmodule",
            1,
        ),
        removed,
        sorted(cuts),
    )


def qualify(*, sources=None, directory=None):
    supplied = sources is not None
    if not supplied and not build.generation_current():
        raise ValueError("Current parent elaboration required")
    directory = directory or build.BBC / "startup-interrupt-quiet-proof"
    directory.mkdir(exist_ok=True)
    sources = sources or [
        (1, build.BBC / "BbcMachine.v", "BbcMachine"),
        (3, video96.DIRECTORY / "BbcMachine96.v", "BbcMachine96"),
    ]
    files = [
        Path(__file__),
        Path(bus_binding_proof.__file__),
        *[
            ROOT / "rtl/bbc" / name
            for name in ("BbcMachine.scala", "Via6522.scala", "BbcSerial.scala", "Intel8271.scala")
        ],
        *[path for _, path, _ in sources],
        *build.BBC.glob("*.bin"),
        *video96.DIRECTORY.glob("*.bin"),
    ]
    before = build.hashes(files)
    clocks = []
    for multiplier, path, top in sources:
        source = path.read_text()
        checks = []
        negative_horizon = max(40, 16 * multiplier + 8)
        for name in ("positive", "initial-via-interrupts-enabled"):
            text = source
            if name != "positive":
                anchor = "enabled <= 7'h0;"
                if source.count(anchor) != 1:
                    raise ValueError("Changed actual VIA reset-mask fault anchor")
                text = source.replace(anchor, "enabled <= 7'h7f;", 1)
            text, removed, cuts = observed(text, top)
            rtl = directory / f"{name}{multiplier}.v"
            rtl.write_text(text)
            graph = directory / f"{name}{multiplier}.json"
            witness = directory / f"{name}{multiplier}-counterexample.json"
            command = (
                f"read_verilog -formal -D SYNTHESIS {rtl}; delete "
                + " ".join(f"{top}/c:{child}" for child in removed)
                + f"; hierarchy -top {top}; proc; delete -output {top}/o:io_*; cutpoint "
                + " ".join(f"{top}/w:{cut}" for cut in cuts)
                + f"; expose {top}/w:proofQuiet {top}/w:proofContract; setattr -unset keep Intel8271/w:mode; opt -purge; flatten; memory_map; opt; async2sync; opt_clean; check -assert; write_json {graph}; "
            )
            if name == "positive":
                command += "sat -set-assumes -set-init-zero -tempinduct -initsteps 2 -maxsteps 12 -prove proofContract 1 -verify -timeout 90"
            else:
                command += f"sat -set-assumes -set-init-zero -seq {negative_horizon} -prove proofQuiet 1 -verify -timeout 90 -dump_json {witness}"
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
                raise ValueError(f"Startup interrupt quiet proof failed: {name}/{multiplier}")
            checks.append({"name": name, "accepted": accepted})
        clocks.append(
            {"clock_multiplier": multiplier, "negative_horizon": negative_horizon, "checks": checks}
        )
    stable = before == build.hashes(files)
    write_json(
        directory / "result.json",
        {
            "passed": stable,
            "inputs": before,
            "inputs_unchanged": stable,
            "root_generation_bound": not supplied,
            "artifacts": build.hashes(
                p for p in directory.iterdir() if p.is_file() and p.name != "result.json"
            ),
            "clocks": clocks,
            "tools": {"yosys": subprocess.check_output(["yosys", "-V"], text=True).strip()},
            "scope": "Both actual parent schedulers, decoder, system/user VIAs, serial and disc controller keep actual CPU IRQ/NMI inactive throughout a cold read-only CPU prefix. Addresses, read data selection, warm requests, analogue/keyboard/video input-pin histories and storage data remain unrestricted. Other child outputs are free abstractions. Only initial reset and the explicit no-CPU-writes prefix fixture constrain inputs; no DUT state assignment. Actual VIA reset-mask faults require initialized counterexamples at both rates.",
            "limits": "A pre-first-write interrupt premise, not a general interrupt-history theorem. It composes with startup's convergence-before-any-write property; peripheral writes, subsequent gameplay and whole-game equivalence retain separate obligations.",
            "universal_game_equivalence": False,
        },
    )
    if not stable:
        raise ValueError("Startup device inputs changed")


if __name__ == "__main__":
    qualify()
