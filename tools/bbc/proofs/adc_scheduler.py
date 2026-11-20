"""Connect actual ADC deadlines to a projected two-MHz scheduler task."""

import json
import re
import shutil
import subprocess
from pathlib import Path

from tools.bbc import bus_binding_proof, video96
from tools.bbc.proofs import pin_bindings
from tools.bbc.proofs.video_cursor import hashes, replace_once
from tools.validation.common import ROOT, source_root, write_json


def observed(source, top, short_delay, long_delay):
    parent = bus_binding_proof.module(source, top)
    adc = bus_binding_proof.module(source, "Adc7002")
    if not all(
        re.search(pattern, adc)
        for pattern in (
            r"reg\s+\[13:0\]\s+conversion\s*;",
            r"reg\s+busy\s*;",
        )
    ):
        raise ValueError("Changed actual ADC countdown state")
    child = re.search(r"\bAdc7002\s+adc\s*\(([\s\S]*?)\n\s*\);", parent)
    if child is None:
        raise ValueError("Missing actual parent ADC instance")
    parent = replace_once(
        parent,
        child[0],
        child[0].replace(
            "Adc7002 adc (", "Adc7002 adc (.proofRemaining(adcRemaining), .proofBusy(adcBusy),"
        ),
    )
    monitor = f"""
wire [13:0] adcRemaining;
wire adcBusy;
reg pending=0;
reg [14:0] taskOffset=0;
wire startConversion=adc_io_write && adc_io_address==0;
wire schedulerFinishing=tickCpu && pending && taskOffset==1;
wire actualFinishing=tickVia && adcBusy && adcRemaining==1;
// Relative task offset projects polltime(1), cancellation and scheduling.
// The original scheduler expires callbacks before a subsequent bus write.
always @(posedge clk or posedge reset)
 if(reset) begin pending<=0;taskOffset<=0;end
 else begin
  if(tickCpu && pending) begin
   if(taskOffset==1) begin pending<=0;taskOffset<=0;end
   else taskOffset<=taskOffset-1'b1;
  end
  if(startConversion) begin
   pending<=1;
   taskOffset<=cpu_write_data[3] ? 15'd{long_delay} : 15'd{short_delay};
  end
 end
wire [14:0] expectedOffset={{adcRemaining,1'b0}}-(phase_1[4] ? 15'd0 : 15'd1);
wire proofDeadline=reset || (
 pending==adcBusy && (!pending || taskOffset==expectedOffset) &&
 schedulerFinishing==actualFinishing &&
 (!(adc_io_read || adc_io_write) || tickVia));
"""
    parent = parent.removesuffix("endmodule") + monitor + "endmodule"
    header = adc.index(");")
    observed_adc = (
        adc[:header] + ", output wire [13:0] proofRemaining, output wire proofBusy\n" + adc[header:]
    )
    observed_adc = replace_once(
        observed_adc,
        "endmodule",
        "assign proofRemaining=conversion;assign proofBusy=busy;\nendmodule",
    )
    old_parent = bus_binding_proof.module(source, top)
    return replace_once(replace_once(source, old_parent, parent), adc, observed_adc)


def qualify(*, parents=None, directory=None):
    original = source_root("jsbeeb") / "src/adc.js"
    text = original.read_text()
    delay = re.findall(r"this\.task\.schedule\(val & 0x08 \? (\d+) : (\d+)\);", text)
    if len(delay) != 1 or "this.task.cancel();\n        this.task.schedule(" not in text:
        raise ValueError("Changed source ADC schedule/cancellation rule")
    long_delay, short_delay = map(int, delay[0])
    if (long_delay, short_delay) != (20000, 8000):
        raise ValueError("Changed nominal ADC conversion profile")
    parents = parents or [
        (1, ROOT / "build/bbc/BbcMachine.v", "BbcMachine"),
        (3, video96.DIRECTORY / "BbcMachine96.v", "BbcMachine96"),
    ]
    if len(parents) != 2 or {(m, top) for m, _, top in parents} != {
        (1, "BbcMachine"),
        (3, "BbcMachine96"),
    }:
        raise ValueError("Expected both declared machine clock variants")
    if len({bus_binding_proof.module(path.read_text(), "Adc7002") for _, path, _ in parents}) != 1:
        raise ValueError("ADC children differ between actual clock variants")
    directory = directory or ROOT / "build/bbc/adc-scheduler-bridge"
    work = directory / "proofs"
    work.mkdir(parents=True, exist_ok=True)
    files = [
        Path(__file__),
        Path(bus_binding_proof.__file__),
        Path(pin_bindings.__file__),
        Path(__file__).with_name("video_cursor.py"),
        original,
        original.with_name("scheduler.js"),
        ROOT / "tools/bbc/reference_adc_results.mjs",
        ROOT / "tools/bbc/reference_adc_sampling.mjs",
        ROOT / "rtl/bbc/Adc7002.scala",
        ROOT / "rtl/bbc/BbcMachine.scala",
        ROOT / "rtl/bbc/BbcAddressDecode.scala",
        *[p for _, p, _ in parents],
        *[p.parent / "elaboration.json" for _, p, _ in parents],
        Path(shutil.which("yosys")),
    ]
    before = hashes(files)
    rows, artifacts = [], []
    for multiplier, path, top in parents:
        source = path.read_text()
        _, removed, cuts = pin_bindings.observe(source, top)
        removed = [n for n in removed if n != "adc"]
        ports = bus_binding_proof.ports(bus_binding_proof.module(source, top), "Adc7002", "adc")
        outputs = {
            ports[n].split("[")[0].strip()
            for n in (
                "io_result",
                "io_eocN",
                "io_eocPulse",
                "io_readRace",
                "io_eocBusN",
            )
        }
        cuts = [n for n in cuts if n not in outputs | {"cpuEnable", "tickVia"}]
        positive = observed(source, top, short_delay, long_delay)
        variants = [
            ("positive", positive),
            ("early-source-deadline", observed(source, top, short_delay - 1, long_delay)),
            (
                "wrong-adc-clock",
                replace_once(
                    positive,
                    ".io_tick     (tickVia            ), //i",
                    ".io_tick     (tickCpu            ), //i",
                ),
            ),
            (
                "lost-slow-gate",
                replace_once(
                    positive,
                    "assign cpuEnable = (tickCpu && ((! decode_io_slow) || (tickVia && delayedSlow)));",
                    "assign cpuEnable = tickCpu;",
                ),
            ),
        ]
        for name, model in variants:
            label = str(multiplier) + "-" + name
            rtl, log, graph, witness = [
                work / (label + suffix)
                for suffix in (
                    ".v",
                    ".log",
                    ".json",
                    "-counterexample.json",
                )
            ]
            rtl.write_text(model)
            witness.unlink(missing_ok=True)
            mode = (
                "-tempinduct -initsteps 2 -maxsteps 10"
                if name == "positive"
                else f"-seq {100 * multiplier}"
            )
            command = (
                f"read_verilog -formal -D SYNTHESIS {rtl}; delete "
                + " ".join(f"{top}/c:{child}" for child in removed)
                + f"; hierarchy -top {top}; proc; delete -output {top}/o:io_*; cutpoint "
                + " ".join(f"{top}/w:{cut}" for cut in cuts)
                + f"; expose {top}/w:proofDeadline; opt -purge; flatten; opt; async2sync; opt_clean; "
                f"check -assert; write_json {graph}; sat -set-init-zero {mode} "
                f"-prove proofDeadline 1 -verify -timeout 120 -show proofDeadline -dump_json {witness}"
            )
            with log.open("w") as output:
                run = subprocess.run(
                    ["yosys", "-Q", "-T", "-p", command],
                    cwd=path.parent,
                    stdout=output,
                    stderr=subprocess.STDOUT,
                    timeout=300,
                )
            if any(
                c["type"] == "$assume"
                for c in json.loads(graph.read_text())["modules"][top]["cells"].values()
            ):
                raise ValueError("ADC scheduler bridge must have no assumptions")
            accepted = (
                run.returncode == 0 and "Induction step proven: SUCCESS!" in log.read_text()
                if name == "positive"
                else run.returncode != 0
                and "ERROR: Called with -verify and proof did fail!" in log.read_text()
                and witness.is_file()
                and any(
                    s["name"] == "proofDeadline" and "0" in s.get("wave", "")
                    for s in json.loads(witness.read_text())["signal"]
                )
            )
            artifacts += [rtl, log, graph, *([witness] if witness.exists() else [])]
            rows.append({"clock_multiplier": multiplier, "name": name, "accepted": accepted})
            print(label + (": PASS" if accepted else ": FAIL"), flush=True)
            if not accepted:
                break
        if not rows[-1]["accepted"]:
            break
    stable = before == hashes(files)
    passed = stable and len(rows) == 8 and all(r["accepted"] for r in rows)
    write_json(
        directory / "result.json",
        {
            "passed": passed,
            "inputs_unchanged": stable,
            "inputs": before,
            "artifacts": hashes(artifacts),
            "proofs": rows,
            "source_delays_2mhz": {"short": short_delay, "long": long_delay},
            "scope": "Initialized induction of actual parent clock/phase, address decoder, slow bus acceptance and complete ADC countdown against a two-MHz relative-offset scheduler projection at both master rates. All CPU output buses and other child outputs are independent free inputs; acceptance, tick and ADC state drivers are retained. Arbitrary starts, cancellation/restart, resets and clock histories. Six actual timing/connection faults require initialized counterexamples; no assumptions.",
            "limits": "The relative-offset scheduler is a declared projection, not a universal translation proof of JavaScript Scheduler or callback ordering. Source delay literals and cancellation order are bound exactly. Global reference epoch alignment, other callbacks, analogue inputs/result semantics, restored/warm reset states and whole-game equivalence remain separate.",
            "universal_game_equivalence": False,
        },
    )
    if not passed:
        raise ValueError("ADC scheduler bridge incomplete; see retained witnesses/logs")


if __name__ == "__main__":
    qualify()
