"""Prove actual integrated CRTC state against a character-history contract."""

import json
import shutil
import subprocess
from pathlib import Path

from tools.bbc import build, bus_binding_proof
from tools.bbc.proofs.video_cursor import hashes, replace_once
from tools.validation.common import ROOT, source_root, write_json


def qualify():
    directory = build.BBC / "crtc-history-contract"
    directory.mkdir(exist_ok=True)
    parents = [build.BBC / "BbcMachine.v", build.BBC / "full-video/BbcMachine96.v"]
    modules = [bus_binding_proof.module(p.read_text(), "Crtc6845") for p in parents]
    if modules[0] != modules[1]:
        raise ValueError("Clock variants contain different CRTC implementations")
    reference = Path(__file__).with_name("crtc_contract.v")
    public = Path(__file__).with_name("crtc_cursor_reference.mjs")
    original = source_root("jsbeeb")
    files = [
        Path(__file__),
        Path(bus_binding_proof.__file__),
        Path(__file__).with_name("video_cursor.py"),
        ROOT / "rtl/bbc/Crtc6845.scala",
        reference,
        public,
        *parents,
        *sorted((original / "src").rglob("*.js")),
        Path(shutil.which("yosys")),
        Path(shutil.which("node")),
    ]
    before = hashes(files)
    public_output = directory / "original-cursor-reference.json"
    subprocess.run(["node", str(public), str(original), str(public_output)], check=True, timeout=90)
    observations = json.loads(public_output.read_text())
    if observations != {"passed": True, "observations": 1048576}:
        raise ValueError("Incomplete original raster/cursor reference observations")
    fields = "horizontal,row,raster,line,adjusting,adjust,lineAddress,nextLineAddress,address,sync,syncLines,hadSync,endMain,checkAdjust,endAdjust,endDummy,frameParity,fieldCount,cursorOn,cursorOff,cursorVisible,fieldOdd,rasterPin0,verticalActive,firstLine,hsync,hcount"
    observed = replace_once(
        modules[0], "module Crtc6845 (", "module Crtc6845 (output wire [105:0] proofState,"
    )
    observed = replace_once(observed, "endmodule", "assign proofState={" + fields + "};\nendmodule")
    contract = directory / "contract.v"
    contract.write_text("""module CrtcContract(input clk,output equivalent);
(* anyseq *) reg reset,enable;
(* anyseq *) reg [143:0] registers;
reg initialReset=1;
always @(posedge clk)begin
 if(initialReset)assume(reset);
 initialReset<=0;
end
wire [105:0] actual,expected;
wire [13:0] actualBus,expectedBus;
wire actualSync,expectedSync,actualFrame,expectedFrame;
Crtc6845 dut(.clk(clk),.reset(reset),.io_enable(enable),.io_registers(registers),
 .proofState(actual),.io_busAddress(actualBus),.io_busVsync(actualSync),.io_frame(actualFrame));
CrtcHistoryReference ref(.clk(clk),.reset(reset),.enable(enable),.registers(registers),
 .state(expected),.busAddress(expectedBus),.busVsync(expectedSync),.frame(expectedFrame));
assign equivalent=actual==expected && actualBus==expectedBus &&
 actualSync==expectedSync && actualFrame==expectedFrame;
endmodule
""")
    mutations = [
        ("positive", observed),
        (
            "lost-horizontal-increment",
            replace_once(
                observed, "horizontal <= (horizontal + 8'h01);", "horizontal <= horizontal;"
            ),
        ),
        ("wrong-cursor-start", replace_once(observed, "cursorOn <= 1'b1;", "cursorOn <= 1'b0;")),
        ("lost-sync-start", replace_once(observed, "nextSync = 1'b1;", "nextSync = 1'b0;")),
        (
            "lost-address-advance",
            replace_once(observed, "nextAddress = (address + 14'h0001);", "nextAddress = address;"),
        ),
    ]
    results = []
    for name, text in mutations:
        rtl = directory / (name + ".v")
        rtl.write_text(text)
        witness = directory / (name + "-counterexample.json")
        witness.unlink(missing_ok=True)
        mode = "-tempinduct -initsteps 3 -maxsteps 12" if name == "positive" else "-seq 12"
        command = f"read_verilog -formal {rtl} {reference} {contract}; prep -top CrtcContract -flatten; check -assert; async2sync; opt_clean; write_json {directory}/{name}.json; sat -set-assumes -set-init-zero {mode} -prove equivalent 1 -verify -timeout 120 -show equivalent -dump_json {witness}"
        log = directory / (name + ".log")
        with log.open("w") as output:
            process = subprocess.run(
                ["yosys", "-Q", "-T", "-p", command],
                cwd=ROOT,
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
            raise ValueError("CRTC history proof failed: " + name)
    stable = before == hashes(files)
    report = {
        "passed": stable,
        "inputs": before,
        "inputs_unchanged": stable,
        "artifacts": build.hashes(
            [p for p in directory.iterdir() if p.is_file() and p.name != "result.json"]
        ),
        "register_bits": 106,
        "clocks": [1, 3],
        "proofs": results,
        "original_cursor_observations": observations["observations"],
        "scope": "All 106 actual CRTC state bits in the identical integrated child at both clock rates, plus pre-edge address/sync/frame forwarding, over arbitrary character enables, live register bytes and reset histories. Actual sequential drivers are retained and observed read-only. Independent imperative character-step equations follow the documented original operation order. Public Original scanline methods separately exercise raster, cursor and field-boundary rules across exhaustive raster/start/end combinations and directed adjust/interlace paths. Four actual RTL mutations require initialized counterexamples.",
        "limits": "A declared character-history contract, not a theorem connecting every step to the independently compiled C or JS video engine. Public cursor observations are finite restored reference fixtures, not arbitrary-history proof of that engine. Register masking, master/character phase, rendering and full-machine composition remain separate. Digital HSYNC equations exclude the JS monitor beam callback. Current parent models retain the old keypad child; this proof depends on their unchanged CRTC child only. No universal game equivalence.",
        "universal_game_equivalence": False,
    }
    write_json(directory / "result.json", report)
    if not stable:
        raise ValueError("CRTC inputs changed")
    return report


if __name__ == "__main__":
    qualify()
