"""Bind accepted CPU and peripheral slots to the reference bus time from reset."""

import re
import shutil
import subprocess
from itertools import groupby
from pathlib import Path

from tools.bbc import bus_binding_proof
from tools.bbc.proofs import current_machine
from tools.bbc.proofs.video_cursor import hashes, replace_once
from tools.validation.common import ROOT, source_root, write_json

DIRECTORY = ROOT / "build/bbc/reference-bus-epoch"


def slow_address(table):
    if len(table) != 65536 or set(table) != {0b0101, 0b1011}:
        raise ValueError("Unexpected model B address/deadline table")
    spans = []
    address = 0
    for value, group in groupby(table):
        stop = address + sum(1 for _ in group)
        if value == 0b1011:
            spans.append(f"(proofAddress>=16'h{address:04x} && proofAddress<16'h{stop:04x})")
        address = stop
    return " || ".join(spans)


def observe(source, top, multiplier, slow):
    parent = bus_binding_proof.module(source, top)
    cpu = bus_binding_proof.ports(parent, "BbcCpu", "cpu")
    adc = bus_binding_proof.ports(parent, "Adc7002", "adc")
    serial = bus_binding_proof.ports(parent, "BbcSerial", "serial")
    audio = bus_binding_proof.ports(parent, "BbcAudio", "audio")
    via = [bus_binding_proof.ports(parent, "Via6522", kind)["io_tick"] for kind in ("sys", "user")]
    if (cpu["address"], cpu["enable"]) != ("cpu_address[15:0]", "cpuEnable"):
        raise ValueError("Unexpected actual CPU bus endpoint")
    children = [
        name for name in re.findall(r"^\s+\w+\s+(\w+)\s*\(\s*$", parent, re.M) if name != "decode"
    ]
    if "cpu" not in children:
        raise ValueError("Missing actual CPU driver to abstract")
    period = 32 * multiplier
    half = period // 2
    actual_offset = f"(phase_1*{multiplier}+_zz_clock32)" if multiplier == 3 else "phase_1"
    actual_divider_valid = "_zz_clock32<3" if multiplier == 3 else "1'b1"
    observer = f"""
(* anyseq *) reg [15:0] nextAddress;
reg [15:0] acceptedAddress;
reg started=0;
reg [6:0] offset=0;
reg [1:0] waiting=0;
initial assume(reset);
wire [15:0] proofAddress=started ? acceptedAddress : nextAddress;
assign cpu_address=proofAddress;
wire refOdd=offset>={half};
wire refSlow={slow};
wire refSlot=offset=={half - 1} || offset=={period - 1};
wire refVia=offset=={half - 1};
wire refClock32=offset%{multiplier}=={multiplier - 1};
wire refAccept=refSlot && (waiting==1 || (waiting==0 && !refSlow));
always @(posedge clk or posedge reset)
 if(reset) begin started<=0;offset<=0;waiting<=0;end
 else begin
  offset<=offset=={period - 1} ? 0 : offset+1'b1;
  if(!started || refAccept)begin acceptedAddress<=nextAddress;started<=1;end
  if(refSlot)begin
   if(waiting!=0)waiting<=waiting-1'b1;
   else if(refSlow)waiting<=refOdd ? 1 : 2;
  end
 end
wire proofEpoch=reset || (
 offset=={actual_offset} && offset<{period} && {actual_divider_valid} &&
 waiting<3 && (waiting!=0)==delayedSlow && (waiting!=2 || refOdd) &&
 (waiting!=1 || !refOdd) && (waiting==0 || refSlow) && (started || offset==0) &&
 tickCpu==refSlot && {serial["io_tick"]}==refSlot &&
 {adc["io_tick"]}==refVia && {via[0]}==refVia && {via[1]}==refVia &&
 {audio["io_clock32"]}==refClock32 &&
 decode_io_slow==refSlow && cpuEnable==refAccept
);
"""
    return source.replace(
        parent, parent.removesuffix("endmodule") + observer + "endmodule", 1
    ), children


def qualify():
    current_machine.audit()
    parents = current_machine.parents()
    original = source_root("jsbeeb") / "src/6502.js"
    adapter = ROOT / "tools/bbc/reference_cycle.mjs"
    reference = ROOT / "tools/bbc/reference.mjs"
    native = ROOT / "sim/native/bbc.cpp"
    runner = Path(__file__).with_suffix(".mjs")
    files = [
        Path(__file__),
        runner,
        original,
        adapter,
        reference,
        native,
        Path(bus_binding_proof.__file__),
        Path(current_machine.__file__),
        ROOT / "build/bbc/equivalence-current/generation.json",
        *[p for _, p, _ in parents],
        *[Path(shutil.which(tool)) for tool in ("node", "yosys")],
    ]
    before = hashes(files)
    if not all(
        text.count(fragment) == 1
        for text, fragment in (
            (reference.read_text(), "cpu.currentCycles += 16;"),
            (reference.read_text(), "cpu.scheduler.polltime(16);"),
            (native.read_text(), "dut.reset = !restoring && cycle < 256;"),
            (adapter.read_text(), "cpu.polltime(1);"),
        )
    ):
        raise ValueError("Changed shared-reset bus epoch")
    table = subprocess.check_output(["node", runner, original, adapter], timeout=60)
    slow = slow_address(table)
    DIRECTORY.mkdir(parents=True, exist_ok=True)
    table_path = DIRECTORY / "original-slots.bin"
    table_path.write_bytes(table)
    artifacts = [table_path]
    for multiplier, path, top in parents:
        source = path.read_text()
        for name, text in (
            ("positive", source),
            ("lost-slow-delay", replace_once(source, "(tickVia && delayedSlow)", "tickVia")),
        ):
            observed, children = observe(text, top, multiplier, slow)
            rtl, log, graph, witness = (
                DIRECTORY / f"{name}-{multiplier}{suffix}"
                for suffix in (".v", ".log", ".json", "-witness.json")
            )
            rtl.write_text(observed)
            witness.unlink(missing_ok=True)
            positive = name == "positive"
            mode = (
                "-tempinduct -initsteps 3 -maxsteps 12"
                if positive
                else f"-seq {32 * multiplier + 2}"
            )
            command = (
                f"read_verilog -formal {rtl}; delete "
                + " ".join(f"{top}/c:{child}" for child in children)
                + f"; hierarchy -top {top}; proc; delete -output {top}/o:io_*; "
                f"expose {top}/w:proofEpoch; opt_clean -purge; flatten; async2sync; opt; "
                f"check -assert; write_json {graph}; "
                f"sat -set-assumes -set-init-zero {mode} -prove proofEpoch 1 -verify "
                f"-timeout 90 -show proofEpoch -dump_json {witness}"
            )
            with log.open("w") as output:
                trial = subprocess.run(
                    ["yosys", "-Q", "-T", "-p", command],
                    cwd=path.parent,
                    stdout=output,
                    stderr=subprocess.STDOUT,
                    timeout=120,
                )
            if positive:
                passed = (
                    trial.returncode == 0 and "Induction step proven: SUCCESS!" in log.read_text()
                )
            else:
                passed = (
                    trial.returncode != 0
                    and witness.is_file()
                    and "model found: FAIL!" in log.read_text()
                )
            if not passed:
                raise ValueError(f"BBC bus epoch failed at {multiplier * 32} MHz: {log}")
            artifacts.extend(p for p in (rtl, log, graph, witness) if p.is_file())
            print(f"BBC bus epoch {multiplier * 32} MHz {name}: PASS", flush=True)
    current_machine.audit()
    if before != hashes(files):
        raise ValueError("BBC bus epoch proof inputs changed")
    write_json(
        DIRECTORY / "result.json",
        {
            "passed": True,
            "inputs_unchanged": True,
            "inputs": before,
            "artifacts": hashes(artifacts),
            "scope": "Both actual parent schedulers agree with the shared-reset reference 2 MHz bus epoch and turnaround rule for arbitrary request sequences. Every request address and clock parity is checked by executing the pinned JavaScript and cycle adapter for reads and writes. The VIA/ADC, serial and sound clock bindings follow the same epoch. A lost slow delay must produce an initialized counterexample at both master rates.",
            "limits": "The CPU is abstracted as one held address per request. Device callback values, interrupt ordering, rendering and game opcode eligibility require separate checks.",
            "universal_game_equivalence": False,
        },
    )


if __name__ == "__main__":
    qualify()
