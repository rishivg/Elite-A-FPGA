"""Prove an integrated bitmap contract across arbitrary live video histories."""

import argparse
import json
import re
import shutil
import subprocess
from pathlib import Path

from tools.bbc import build, bus_binding_proof
from tools.bbc.proofs import memory_composition
from tools.bbc.proofs.video_cursor import hashes, replace_once
from tools.validation.common import ROOT, write_json


def qualify(multiplier=1):
    directory = build.BBC / f"bitmap{32 * multiplier}-history-contract"
    directory.mkdir(exist_ok=True)
    parent = build.BBC / ("BbcMachine.v" if multiplier == 1 else "full-video/BbcMachine96.v")
    crtc_receipt = build.BBC / "crtc-history-contract/result.json"
    memory_composition.sealed(crtc_receipt)
    reference = Path(__file__).with_name("bitmap_contract.v")
    crtc_reference = Path(__file__).with_name("crtc_contract.v")
    files = [
        Path(__file__),
        Path(bus_binding_proof.__file__),
        Path(memory_composition.__file__),
        Path(__file__).with_name("video_cursor.py"),
        reference,
        crtc_reference,
        crtc_receipt,
        parent,
        ROOT / "rtl/bbc/BbcVideo.scala",
        ROOT / "rtl/bbc/BbcVideoAddress.scala",
        ROOT / "rtl/bbc/Crtc6845.scala",
        Path(shutil.which("yosys")),
        *parent.parent.glob("*.bin"),
    ]
    before = hashes(files)
    full = parent.read_text()
    crtc = bus_binding_proof.module(full, "Crtc6845")
    fields = "horizontal,row,raster,line,adjusting,adjust,lineAddress,nextLineAddress,address,sync,syncLines,hadSync,endMain,checkAdjust,endAdjust,endDummy,frameParity,fieldCount,cursorOn,cursorOff,cursorVisible,fieldOdd,rasterPin0,verticalActive,firstLine,hsync,hcount"
    observed_crtc = replace_once(
        crtc, "module Crtc6845 (", "module Crtc6845 (output wire [105:0] proofState,"
    )
    observed_crtc = replace_once(
        observed_crtc, "endmodule", "assign proofState={" + fields + "};\nendmodule"
    )
    video = bus_binding_proof.module(full, "BbcVideo")
    observed_video = replace_once(
        video,
        "module BbcVideo (",
        "module BbcVideo (output wire [354:0] proofState,output wire [269:0] proofOutputs,",
    )
    observed_video = replace_once(
        observed_video, "Crtc6845 crtc (", "Crtc6845 crtc (.proofState(proofCrtc),"
    )
    divider = "2'b0" if multiplier == 1 else "_zz_clock32"
    packed_regs = ",".join(f"writableRegs_{i}" for i in reversed(range(16)))
    contents = ",".join(f"(paletteValid[{i}]?palette[{i}]:4'b0)" for i in reversed(range(16)))
    additions = f"""
wire [105:0] proofCrtc;
assign proofState={{proofCrtc,index,control,{{{packed_regs}}},lightPen,
 {{{contents}}},master,{divider},cellHorizontal,cellLine,cellDisplay,cursorIndex,cellCursor}};
assign proofOutputs={{io_result,io_registers,io_palette,io_control,io_memoryAddress,io_memoryRead,
 (control[1]?1'b0:io_pixel),(control[1]?1'b0:io_display),(control[1]?3'b0:io_rgb),
 (control[1]?11'b0:io_x),(control[1]?10'b0:io_y),io_hsync,io_vsync,io_busVsync,io_frame}};
"""
    observed_video = replace_once(observed_video, "endmodule", additions + "endmodule")
    if multiplier == 3:
        # Only excluded TT output assignments are replaced. All bitmap state,
        # CRTC timing and register updates retain their actual drivers.
        for port, width in (("display", 1), ("pixel", 1), ("x", 11), ("y", 10), ("rgb", 3)):
            pattern = rf"(if\(when_BbcVideo_l228\) begin\s+io_{port} = )[^;]+;"
            observed_video, count = re.subn(pattern, rf"\g<1>{width}'b0;", observed_video)
            if count != 1:
                raise ValueError("Changed excluded teletext output anchor")
    source = replace_once(replace_once(full, crtc, observed_crtc), video, observed_video)
    contract = directory / "contract.v"
    contract.write_text(f"""module BitmapContract(input clk,output equivalent);
(* anyseq *) reg reset,crtcWrite,lightPenPulse,ulaWrite,address;
(* anyseq *) reg [7:0] data,memoryData;
(* anyseq *) reg [1:0] scroll;
reg initialReset=1;
always @(posedge clk)begin if(initialReset)assume(reset);initialReset<=0;end
wire [354:0] actualState,expectedState;
wire [269:0] actualOutputs,expectedOutputs;
BbcVideo dut(.clk(clk),.reset(reset),.io_crtcWrite(crtcWrite),.io_lightPenPulse(lightPenPulse),
 .io_ulaWrite(ulaWrite),.io_address(address),.io_data(data),.io_memoryData(memoryData),
 .io_scroll(scroll),.proofState(actualState),.proofOutputs(actualOutputs));
BitmapReference #(.MULTIPLIER({multiplier})) ref(.clk(clk),.reset(reset),
 .crtcWrite(crtcWrite),.lightPenPulse(lightPenPulse),.ulaWrite(ulaWrite),.address(address),
 .data(data),.memoryData(memoryData),.scroll(scroll),.proofState(expectedState),.observations(expectedOutputs));
// The cursor has seven legal segment states; prove this reachable invariant
// rather than assuming an arbitrary hidden three-bit value is legal.
assign equivalent=actualState==expectedState && actualOutputs==expectedOutputs &&
 actualState[3:1]<7;
endmodule
""")
    faults = [
        ("positive", source),
        (
            "lost-master-advance",
            replace_once(source, "master <= (master + 5'h01);", "master <= master;"),
        ),
        (
            "lost-pixel-strobe",
            replace_once(source, "io_pixel = (clock32 && master[0]);", "io_pixel = 1'b0;"),
        ),
        (
            "wrong-memory-address",
            replace_once(
                source,
                "assign io_memoryAddress = memory_io_address;",
                "assign io_memoryAddress = memory_io_address ^ 15'b1;",
            ),
        ),
        (
            "lost-lightpen-capture",
            replace_once(source, "lightPen <= crtc_io_busAddress;", "lightPen <= lightPen;"),
        ),
    ]
    results = []
    for name, text in faults:
        rtl = directory / (name + ".v")
        rtl.write_text(text)
        witness = directory / (name + "-counterexample.json")
        witness.unlink(missing_ok=True)
        depth = (24 if name == "lost-lightpen-capture" else 8) * multiplier
        mode = "-tempinduct -initsteps 4 -maxsteps 12" if name == "positive" else f"-seq {depth}"
        proof_contract = contract
        if name == "lost-lightpen-capture":
            # Search for the actual public capture failure alone. The complete
            # positive theorem still includes every semantic state/output bit.
            proof_contract = directory / (name + "-contract.v")
            proof_contract.write_text(
                replace_once(
                    contract.read_text(),
                    "actualState==expectedState && actualOutputs==expectedOutputs &&\n actualState[3:1]<7",
                    "actualState[107:94]==expectedState[107:94]",
                )
            )
        command = f"read_verilog -formal {rtl} {crtc_reference} {reference} {proof_contract}; prep -top BitmapContract -flatten; check -assert; memory_map; opt; async2sync; opt_clean; write_json {directory}/{name}.json; sat -set-assumes -set-init-zero {mode} -prove equivalent 1 -verify -timeout 120 -show equivalent -dump_json {witness}"
        log = directory / (name + ".log")
        with log.open("w") as output:
            process = subprocess.run(
                ["yosys", "-Q", "-T", "-p", command],
                cwd=parent.parent,
                stdout=output,
                stderr=subprocess.STDOUT,
                timeout=240,
            )
        content = log.read_text()
        passed = (
            process.returncode == 0 and "Induction step proven: SUCCESS!" in content
            if name == "positive"
            else process.returncode != 0
            and "ERROR: Called with -verify and proof did fail!" in content
            and witness.is_file()
            and any(
                s["name"] == "equivalent" and "0" in s.get("wave", "")
                for s in json.loads(witness.read_text())["signal"]
            )
        )
        results.append({"name": name, "accepted": passed})
        if not passed:
            raise ValueError("Integrated bitmap history proof failed: " + name)
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
            "proofs": results,
            "clock_multiplier": multiplier,
            "scope": "Integrated bitmap history contract, including actual CRTC, masked register/ULA bus, palette, master scheduling, memory address selection, held coordinates/display and cursor state. Arbitrary live register/palette writes, RAM response bytes, scroll/light-pen events and reset after initial cold reset, including bitmap/teletext selection changes. Only TT-dependent output assignments are abstracted and TT rendering is excluded. Read-only semantic state strengthens induction; no DUT state injection.",
            "limits": "Declared digital reference equations, not a formal proof of the independent Original engine. TT rendering, reference absolute phase alignment, independent register/device event timing and whole-machine composition remain separate. Generated parent models retain the old keypad child. No universal game equivalence.",
            "universal_game_equivalence": False,
        },
    )
    if not stable:
        raise ValueError("Bitmap history inputs changed")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--clock-multiplier", type=int, choices=(1, 3), default=1)
    qualify(parser.parse_args().clock_multiplier)
