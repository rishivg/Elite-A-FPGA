"""Prove the released OS startup forgets the cold zero flag before any write."""

import copy
import json
import re
import subprocess
from pathlib import Path

from tools.bbc import build
from tools.bbc.proofs import enable_composition
from tools.validation.common import ROOT, write_json


def qualify():
    directory = build.BBC / "os-startup-zero-flag-bridge"
    directory.mkdir(exist_ok=True)
    premise = build.BBC / "cpu-nmi-idle-summary/result.json"
    enable_composition.sealed(premise)
    raw = build.BBC / "cpu-nmi-idle-summary/positive/registers.json"
    rom = build.BBC / "rom.hex"
    image = bytes(int(x, 16) for x in rom.read_text().split())
    pc = image[0x3FFC] | (image[0x3FFD] << 8)
    assert pc == 0xD9CD and image[pc - 0xC000 : pc - 0xC000 + 2] == bytes([0xA9, 0x40])
    files = [
        Path(__file__),
        Path(enable_composition.__file__),
        premise,
        raw,
        rom,
        ROOT / "rtl/bbc/BbcCpu.v",
        build.BBC / "ArletCpuEnabled.v",
        build.BBC / "reference-cycle/m6502.h",
    ]
    before = build.hashes(files)
    graph = json.loads(raw.read_text())
    actual = graph["modules"]["BbcCpu"]
    state = [
        b
        for n, c in sorted(actual["cells"].items())
        if c["type"] in {"$dff", "$adff"}
        for b in c["connections"]["Q"]
    ]
    assert len(state) == len(set(state)) == 168
    actual["ports"]["proofAllState"] = {"direction": "output", "bits": state}
    actual["netnames"]["proofAllState"] = {"hide_name": 0, "bits": state, "attributes": {}}
    for port, name in (
        ("proofPhase", "core.state"),
        ("proofWarmSecond", "warm_second"),
        ("proofResetPhase", "core.res"),
        ("proofLoad", "core.load_reg"),
        ("proofLoadOnly", "core.load_only"),
        ("proofRegister", "core.regsel"),
        ("proofAdd", "core.ADD"),
    ):
        actual["ports"][port] = {"direction": "output", "bits": actual["netnames"][name]["bits"]}
    special = (
        "plp",
        "compare",
        "bit_ins",
        "adc_sbc",
        "shift",
        "sec",
        "clc",
        "sed",
        "cld",
        "clv",
        "write_back",
        "php",
    )
    actual["ports"]["proofSpecial"] = {
        "direction": "output",
        "bits": [actual["netnames"]["core." + name]["bits"][0] for name in special],
    }
    interrupts = [
        bit
        for name, net in sorted(actual["netnames"].items())
        if (name.startswith("core.bbc_") and len(net["bits"]) == 1)
        or name in {"core.NMI_edge", "core.NMI_1"}
        for bit in net["bits"]
    ]
    assert len(interrupts) == 15
    actual["ports"]["proofInterrupts"] = {"direction": "output", "bits": interrupts}
    reference = copy.deepcopy(actual)
    zero = reference["ports"]["flags"]["bits"][1]
    changed = []
    for name, net in reference["netnames"].items():
        if zero in net["bits"] and "init" in net.get("attributes", {}):
            assert net["bits"] == [zero] and net["attributes"]["init"] == "0"
            net["attributes"]["init"] = "1"
            changed.append(name)
    assert changed == ["core.Z"]
    zero_index = state.index(zero)
    model = {"modules": {"StartupActual": actual, "StartupReference": reference}}
    netlist = directory / "observed.json"
    netlist.write_text(json.dumps(model))
    contract = directory / "contract.v"
    contract.write_text(
        """module StartupBridge(input clk,output equivalent);
reg [3:0] resetEdges=0;
wire reset=resetEdges<8;
(* anyseq *)reg warm;
(* anyseq *)reg [7:0] otherRead;
wire [15:0] aAddress,rAddress;
wire [7:0] aData,rData;
wire aWrite,rWrite,aSync,rSync;
wire [7:0] aFlags,rFlags,aAccumulator,aX,aY;
wire [5:0] phase;
wire warmSecond,resetPhase;
wire load,loadOnly;
wire [1:0] regsel;
wire [7:0] add;
wire [11:0] special;
wire [14:0] interrupts;
wire [167:0] aState,rState;
reg [7:0] readData=0;
reg joined=0;
wire joining=phase==12 && load && loadOnly && regsel==0 && add==8'h40;
wire prejoinPhase=(phase>=8 && phase<=11)||phase==22||phase==23||phase==12||phase==13;
wire prejoinReset=(phase<8 || phase>11) && phase!=22 && phase!=23 || resetPhase;
wire prejoinRegisters=!aFlags[1] && rFlags[1] && (aFlags&8'hc9)==0 &&
 aAccumulator==0 && aX==0 && aY==0;
wire prejoinDecode=special==0 && (!load || (loadOnly && regsel==0 &&
 (phase==13 || joining)));
always @(posedge clk)begin
 if(reset)begin resetEdges<=resetEdges+1;readData<=0;joined<=0;end
 else begin
  if(joining)joined<=1;
  case(rAddress)
   16'hfffc:readData<=8'hcd;
   16'hfffd:readData<=8'hd9;
   16'hd9cd:readData<=8'ha9;
   16'hd9ce:readData<=8'h40;
   default:readData<=otherRead;
  endcase
 end
end
StartupActual actual(.clk(clk),.reset(reset),.enable(1'b1),.irq(1'b0),.nmi(1'b0),.warm_reset(warm),.read_data(readData),.address(aAddress),.write_data(aData),.write_enable(aWrite),.instruction(aSync),.proofAllState(aState),.proofPhase(phase),.proofWarmSecond(warmSecond),.proofResetPhase(resetPhase),.proofLoad(load),.proofLoadOnly(loadOnly),.proofRegister(regsel),.proofAdd(add),.proofSpecial(special),.proofInterrupts(interrupts),.flags(aFlags),.accumulator(aAccumulator),.index_x(aX),.index_y(aY));
StartupReference reference(.clk(clk),.reset(reset),.enable(1'b1),.irq(1'b0),.nmi(1'b0),.warm_reset(warm),.read_data(readData),.address(rAddress),.write_data(rData),.write_enable(rWrite),.instruction(rSync),.proofAllState(rState),.flags(rFlags));
assign equivalent=reset || (interrupts==0 && ((aState^rState)&STATE_MASK)==0 && (aState==rState || (prejoinRegisters && prejoinDecode && prejoinPhase && prejoinReset && (phase!=12 || warmSecond || rAddress==16'hd9ce || joining))) && aAddress==rAddress && aWrite==rWrite && aSync==rSync && (!aWrite || aData==rData) && (!(joined||aWrite)||aState==rState));
endmodule
""".replace("STATE_MASK", f"168'h{((1 << 168) - 1) ^ (1 << zero_index):042x}")
    )
    command = f"read_json {netlist}; read_verilog -formal {contract}; prep -top StartupBridge -flatten; check -assert; async2sync; opt_clean; write_json {directory}/graph.json; sat -set-init-zero -tempinduct -initsteps 1 -maxsteps 32 -prove equivalent 1 -verify -timeout 120 -show joined -show joining -show aAddress -show rAddress -show phase -dump_json {directory}/counterexample.json"
    with (directory / "proof.log").open("w") as out:
        result = subprocess.run(
            ["yosys", "-Q", "-T", "-p", command],
            cwd=ROOT,
            stdout=out,
            stderr=subprocess.STDOUT,
            timeout=2400,
        )
    if (
        result.returncode != 0
        or "Induction step proven: SUCCESS!" not in (directory / "proof.log").read_text()
    ):
        raise ValueError("OS startup zero-flag bridge failed")
    stable = before == build.hashes(files)
    write_json(
        directory / "result.json",
        {
            "passed": stable,
            "inputs": before,
            "inputs_unchanged": stable,
            "artifacts": build.hashes(
                [p for p in directory.iterdir() if p.is_file() and p.name != "result.json"]
            ),
            "register_bits": 168,
            "excluded_register_bits": 0,
            "reference_initial_zero_bit": 1,
            "actual_initial_zero_bit": 0,
            "only_reference_difference": changed,
            "reset_vector": pc,
            "first_instruction": "LDA #$40",
            "induction_depth": int(
                re.findall(
                    r"Trying induction with length (\d+)", (directory / "proof.log").read_text()
                )[-1]
            ),
            "tools": {"yosys": subprocess.check_output(["yosys", "-V"], text=True).strip()},
            "scope": "Cold zero-flag independence for the verified released OS entry. Actual physical CPU unchanged; reference copy differs only in one initial flag attribute. Entry bus requests and all writes agree for arbitrary warm requests. Every physical state bit agrees after the first LDA retirement edge, including a simultaneous warm request, and before every write. Only the four verified vector/opcode/operand bytes are fixed; other read data is free. Read-only startup phase, decode, register and inactive-interrupt invariants strengthen the property; no input assumption or DUT state injection.",
            "limits": "Enable-one ROM startup bridge, zero otherwise-specified power-on fixture, IRQ/NMI inactive. This is initial-profile normalization, not an independent full CPU reference or a parent/device/game proof. Subsequent cold reset inactive.",
            "universal_game_equivalence": False,
        },
    )
    if not stable:
        raise ValueError("Startup inputs changed")


if __name__ == "__main__":
    qualify()
