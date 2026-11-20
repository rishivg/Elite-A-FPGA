"""Prove actual VIA flag/mask state and IRQ/readback for arbitrary event masks."""

import json
import re
import shutil
import subprocess
from pathlib import Path

from tools.bbc import build, bus_binding_proof, video96
from tools.bbc.proofs.video_cursor import hashes, replace_once
from tools.validation.common import ROOT, source_root, write_json


def boundary(source, name, value):
    """Replace exactly one combinational event-mask driver, never a register."""
    matches = list(re.finditer(r"always @\(\*\) begin\s+" + name + r" =", source))
    if len(matches) != 1:
        raise ValueError(f"Changed VIA event boundary: {name}")
    match = matches[0]
    depth = 0
    for token in re.finditer(r"\b(begin|end)\b", source[match.start() :]):
        depth += 1 if token[0] == "begin" else -1
        if depth == 0:
            end = match.start() + token.end()
            return source[: match.start()] + f"always @(*) begin {name}={value}; end" + source[end:]
    raise ValueError("Unterminated VIA event driver")


def qualify():
    if not build.generation_current():
        raise ValueError("Current parent elaboration required")
    directory = build.BBC / "via-interrupt-state-contract"
    directory.mkdir(exist_ok=True)
    parents = [build.BBC / "BbcMachine.v", video96.DIRECTORY / "BbcMachine96.v"]
    modules = [bus_binding_proof.module(path.read_text(), "Via6522") for path in parents]
    if modules[0] != modules[1]:
        raise ValueError("Different actual VIA modules at the two parent clocks")
    original = source_root("jsbeeb")
    reference = Path(__file__).with_name("via_irq_reference.mjs")
    files = [
        Path(__file__),
        Path(bus_binding_proof.__file__),
        Path(__file__).with_name("video_cursor.py"),
        ROOT / "rtl/bbc/Via6522.scala",
        reference,
        *parents,
        *sorted((original / "src").rglob("*.js")),
        Path(shutil.which("yosys")),
        Path(shutil.which("node")),
        *build.BBC.glob("*.bin"),
        *video96.DIRECTORY.glob("*.bin"),
    ]
    before = hashes(files)
    golden = directory / "original-reference.json"
    subprocess.run(["node", str(reference), str(original), str(golden)], check=True, timeout=60)
    if json.loads(golden.read_text()) != {"passed": True, "observations": 311296}:
        raise ValueError("Incomplete public Original interrupt register observations")
    actual = modules[0]
    header = actual.index(");")
    end = actual.rindex("endmodule")
    observed = (
        actual[:header]
        + ", input wire [6:0] proofSet,proofClear, output wire [13:0] proofState\n"
        + actual[header:end]
        + "assign proofState={enabled,flags};\nendmodule"
    )
    observed = boundary(observed, "setFlags", "proofSet")
    observed = boundary(observed, "clearFlags", "proofClear")
    full = parents[0].read_text()
    source = replace_once(full, actual, observed)
    contract = directory / "contract.v"
    contract.write_text("""module InterruptContract(input clk,output equivalent);
(* anyseq *) reg reset,tick,write,read,late;
(* anyseq *) reg [3:0] address;
(* anyseq *) reg [7:0] data;
(* anyseq *) reg [6:0] incoming,acknowledge;
wire [13:0] state;
wire irq;
wire [7:0] result;
Via6522 actual(.clk(clk),.reset(reset),.io_tick(tick),.io_write(write),.io_read(read),
.io_address(address),.io_data(data),.io_ca2AfterWrite(late),
.io_phi2(1'b0),.io_ca1(1'b0),.io_ca2(1'b0),.io_cb1(1'b0),.io_cb1Pulse(1'b0),.io_cb2(1'b0),
.io_portAIn(8'b0),.io_portBIn(8'b0),.proofSet(incoming),.proofClear(acknowledge),
.proofState(state),.io_irq(irq),.io_result(result));
reg [6:0] mask=0,pending=0;
wire [6:0] nextMask=write && address==14 ?
 (data[7] ? mask|data[6:0] : mask&~data[6:0]) : mask;
// Acknowledgement follows incoming events; a timer's just-hit event and
// a GPIO-write-generated CA2 edge survive acknowledgement.
wire [6:0] nextPending=((pending|incoming)&~acknowledge) |
 (incoming&7'h60) | (late && incoming[0] ? 7'h01 : 7'h00);
wire expectedIrq=|(nextMask&nextPending);
always @(posedge clk or posedge reset)begin
 if(reset)begin mask<=0;pending<=0;end
 else begin mask<=nextMask;pending<=nextPending;end
end
wire [7:0] expectedFlags={expectedIrq,tick ? nextPending : pending};
assign equivalent=state=={mask,pending} && irq==expectedIrq &&
 (address!=13 || result==expectedFlags) &&
 (address!=14 || result=={1'b1,mask});
endmodule
""")
    variants = (
        ("positive", source),
        (
            "lost-mask-set",
            replace_once(source, "enabled <= (enabled | io_data[6 : 0]);", "enabled <= enabled;"),
        ),
        (
            "lost-timer-priority",
            replace_once(source, "| (setFlags & 7'h60)", "| 7'h00"),
        ),
        (
            "delayed-irq",
            replace_once(
                source,
                "assign io_irq = (|(nextFlags & nextEnabled));",
                "assign io_irq = (|(flags & enabled));",
            ),
        ),
    )
    rows = []
    for name, text in variants:
        rtl = directory / (name + ".v")
        rtl.write_text(text)
        witness = directory / (name + "-counterexample.json")
        proof = "-tempinduct -initsteps 3 -maxsteps 10" if name == "positive" else "-seq 8"
        command = f"read_verilog -formal {rtl} {contract}; prep -top InterruptContract -flatten; check -assert; memory_map; opt; async2sync; opt_clean; write_json {directory}/{name}.json; sat -set-init-zero {proof} -prove equivalent 1 -verify -timeout 120 -show equivalent -dump_json {witness}"
        log = directory / (name + ".log")
        with log.open("w") as output:
            process = subprocess.run(
                ["yosys", "-Q", "-T", "-p", command],
                cwd=parents[0].parent,
                stdout=output,
                stderr=subprocess.STDOUT,
                timeout=240,
            )
        accepted = (
            process.returncode == 0 and "Induction step proven: SUCCESS!" in log.read_text()
            if name == "positive"
            else process.returncode != 0
            and "ERROR: Called with -verify and proof did fail!" in log.read_text()
            and witness.is_file()
            and any(
                signal["name"] == "equivalent" and "0" in signal.get("wave", "")
                for signal in json.loads(witness.read_text())["signal"]
            )
        )
        rows.append({"name": name, "accepted": accepted})
        if not accepted:
            raise ValueError(f"VIA interrupt contract failed: {name}")
    stable = before == hashes(files)
    write_json(
        directory / "result.json",
        {
            "passed": stable,
            "inputs": before,
            "inputs_unchanged": stable,
            "artifacts": build.hashes(
                [p for p in directory.iterdir() if p.is_file() and p.name != "result.json"]
            ),
            "proofs": rows,
            "register_bits": 14,
            "original_reference_observations": 311296,
            "clocks": [1, 3],
            "scope": "Actual identical VIA modules in both parents: all seven interrupt flags and seven mask bits, IER writes, IRQ forwarding and IFR/IER readback over unbounded clocks. Arbitrary reset, register requests, tick, incoming event/acknowledgement masks and post-write CA2 ordering. Only combinational event-mask producers are abstracted; actual sequential drivers and output forwarding are retained. Public Original methods check all flag/mask readback combinations, every IER write and all IFR acknowledge masks with timer just-hit combinations.",
            "limits": "Event/acknowledgement production, pin transitions, timers, shifter, parent clock phases and full-machine/reference composition retain separate contracts. The public reference uses independent restored fixtures, never DUT state. Not universal whole VIA or game equivalence.",
            "universal_game_equivalence": False,
        },
    )
    if not stable:
        raise ValueError("VIA interrupt inputs changed")


if __name__ == "__main__":
    qualify()
