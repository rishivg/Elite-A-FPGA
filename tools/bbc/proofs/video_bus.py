"""Prove actual CRTC register/light-pen state and CPU readback at both clocks."""

import argparse
import json
import shutil
import subprocess
from pathlib import Path

from tools.bbc import build, bus_binding_proof, crtc_registers, video96
from tools.bbc.proofs.video_cursor import hashes, replace_once
from tools.validation.common import ROOT, digest, source_root, write_json


def qualify(clock_multiplier=3):
    if not build.generation_current():
        raise ValueError("Current parent elaboration required")
    top = "BbcVideo"
    parent = (
        video96.DIRECTORY / "BbcMachine96.v"
        if clock_multiplier == 3
        else build.BBC / "BbcMachine.v"
    )
    if clock_multiplier == 3:
        snapshot = video96.snapshot()
        emitted = json.loads((video96.DIRECTORY / "elaboration.json").read_text())
        if emitted["inputs"] != snapshot["inputs"] or emitted["outputs"] != snapshot["outputs"]:
            raise ValueError("Current 96 MHz video elaboration required")
    directory = build.BBC / f"video{32 * clock_multiplier}-register-contract"
    directory.mkdir(exist_ok=True)
    original = source_root("jsbeeb")
    reference = Path(__file__).with_name("video_bus_reference.mjs")
    corpus = crtc_registers.CORPUS
    oracle = crtc_registers.DIRECTORY / "oracle"
    if not corpus.is_file() or not oracle.is_file():
        crtc_registers.qualify()
    for name, expected in crtc_registers.PIN.items():
        if digest(crtc_registers.DIRECTORY / "sources" / name) != expected:
            raise ValueError("Changed pinned independent C register source")
    files = [
        Path(__file__),
        Path(__file__).with_name("video_cursor.py"),
        Path(bus_binding_proof.__file__),
        Path(crtc_registers.__file__),
        ROOT / "rtl/bbc/BbcVideo.scala",
        ROOT / "sim/native/bbc_crtc_register_oracle.c",
        ROOT / "sim/native/bbc_crtc_register_support.c",
        reference,
        parent,
        corpus,
        oracle,
        *[crtc_registers.DIRECTORY / "sources" / name for name in crtc_registers.PIN],
        *sorted((original / "src").rglob("*.js")),
        Path(shutil.which("yosys")),
        Path(shutil.which("node")),
        *build.BBC.glob("*.bin"),
        *video96.DIRECTORY.glob("*.bin"),
    ]
    before = hashes(files)
    # Re-execute the separately compiled unchanged C API, rather than trust
    # a historical comparison verdict or infer read-only behavior from JS.
    c_result = directory / "c-register-reference.bin"
    subprocess.run([str(oracle), str(c_result)], check=True, timeout=30)
    if c_result.read_bytes() != corpus.read_bytes() or c_result.stat().st_size != 16384:
        raise ValueError("Changed independent C register corpus")
    golden = directory / "original-reference.json"
    subprocess.run(["node", str(reference), str(original), str(golden)], check=True, timeout=60)
    result = json.loads(golden.read_text())
    if result.get("passed") is not True or result.get("observations") != 69632:
        raise ValueError("Incomplete original register/light-pen observations")
    masks = result["masks"]
    if len(masks) != 16:
        raise ValueError("Incomplete independent writable masks")
    c_bytes = c_result.read_bytes()
    for index in range(32):
        for data in range(256):
            expected = data & masks[index] if 12 <= index < 16 else 0
            position = (index * 256 + data) * 2
            if c_bytes[position : position + 2] != bytes((0, expected)):
                raise ValueError("Independent C readable/read-only rules changed")
    full = parent.read_text()
    actual = bus_binding_proof.module(full, top)
    observed = replace_once(
        actual,
        f"module {top} (",
        f"module {top} (input wire [13:0] proofAddress, output wire [4:0] proofIndex,",
    )
    observed = replace_once(
        observed, "lightPen <= crtc_io_busAddress;", "lightPen <= proofAddress;"
    )
    observed = observed.removesuffix("endmodule") + "assign proofIndex=index;\nendmodule"
    source = replace_once(full, actual, observed)
    mask_word = sum(mask << (8 * index) for index, mask in enumerate(masks))
    contract = directory / "contract.v"
    contract.write_text(f"""module RegisterContract(input clk,output equivalent);
(* anyseq *) reg reset,write,address,pulse;
(* anyseq *) reg [7:0] data;
(* anyseq *) reg [13:0] capturedAddress;
wire [4:0] actualIndex;
wire [143:0] actualRegisters;
wire [7:0] actualRead;
{top} actual(.clk(clk),.reset(reset),.io_crtcWrite(write),.io_address(address),.io_data(data),
.io_lightPenPulse(pulse),.proofAddress(capturedAddress),.proofIndex(actualIndex),
.io_registers(actualRegisters),.io_result(actualRead),.io_ulaWrite(1'b0),
.io_memoryData(8'b0),.io_scroll(2'b0));
reg [4:0] index=0;
reg [127:0] registers=0;
reg [13:0] lightPen=0;
localparam [127:0] masks=128'h{mask_word:032x};
always @(posedge clk or posedge reset)begin
 if(reset)begin index<=0;registers<=0;lightPen<=0;end
 else begin
  if(write && !address)index<=data[4:0];
  if(write && address && index<16)registers[8*index+:8]<=data & (masks>>(8*index));
  if(pulse)lightPen<=capturedAddress;
 end
end
wire [143:0] expectedRegisters={{lightPen[7:0],2'b0,lightPen[13:8],registers}};
wire [7:0] expectedRead=address && index>=12 && index<18 ? expectedRegisters[8*index+:8] : 0;
assign equivalent=actualIndex==index && actualRegisters==expectedRegisters && actualRead==expectedRead;
endmodule
""")
    variants = (
        ("positive", source),
        (
            "lost-address-capture",
            replace_once(source, "lightPen <= proofAddress;", "lightPen <= lightPen;"),
        ),
        (
            "wrong-index-mask",
            replace_once(source, "index <= io_data[4 : 0];", "index <= {1'b0,io_data[3 : 0]};"),
        ),
        (
            "writable-light-pen-register",
            replace_once(
                source,
                "      if(io_lightPenPulse) begin",
                "      if(io_crtcWrite && io_address && index==16) begin lightPen <= {io_data[5:0],lightPen[7:0]}; end\n      if(io_lightPenPulse) begin",
            ),
        ),
        (
            "lost-display-width-write",
            replace_once(
                source, "writableRegs_1 <= (io_data & 8'hff);", "writableRegs_1 <= writableRegs_1;"
            ),
        ),
    )
    rows = []
    for name, text in variants:
        rtl = directory / (name + ".v")
        rtl.write_text(text)
        witness = directory / (name + "-counterexample.json")
        proof = "-tempinduct -initsteps 3 -maxsteps 10" if name == "positive" else "-seq 8"
        command = f"read_verilog -formal {rtl} {contract}; prep -top RegisterContract -flatten; check -assert; memory_map; opt; async2sync; opt_clean; write_json {directory}/{name}.json; sat -set-init-zero {proof} -prove equivalent 1 -verify -timeout 120 -show equivalent -dump_json {witness}"
        log = directory / (name + ".log")
        with log.open("w") as output:
            process = subprocess.run(
                ["yosys", "-Q", "-T", "-p", command],
                cwd=parent.parent,
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
            raise ValueError(f"Video register contract failed: {name}")
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
            "clock_multiplier": clock_multiplier,
            "original_reference_observations": 69632,
            "independent_c_bus_reads": 16384,
            "scope": "Actual generated CRTC index, all writable register bits, fourteen light-pen address bits and CPU readback over unbounded clocks. Arbitrary reset, accepted register writes, data bytes, light-pen pulses and abstracted captured addresses; actual state/update priority is retained. Unchanged Original public methods exhaustively check writable masks and light-pen captures; separately executed pinned independent C public APIs establish readable/read-only/unmapped register rules. Four actual register mutations require initialized counterexamples, including incorrectly writable light-pen registers.",
            "limits": "Declared cold device reset. The captured-address producer, VIA pulse generation, CRTC counters, rendering and parent bus timing remain separate. Original JS incorrectly accepts R16/R17 writes; the independent C read-only rule is authoritative here. Not universal equivalence to all JS behavior or the whole game.",
            "universal_game_equivalence": False,
        },
    )
    if not stable:
        raise ValueError("Video register inputs changed")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--clock-multiplier", type=int, choices=(1, 3), default=3)
    qualify(parser.parse_args().clock_multiplier)
