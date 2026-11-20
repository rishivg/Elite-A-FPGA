"""Check reset dummy-read independence using every physical CPU state bit."""

import copy
import json
import subprocess
from pathlib import Path

from tools.bbc import build
from tools.bbc.proofs import enable_composition
from tools.validation.common import ROOT, write_json


def qualify():
    directory = build.BBC / "cpu-reset-read-bridge"
    directory.mkdir(exist_ok=True)
    premise = build.BBC / "cpu-nmi-idle-summary/result.json"
    enable_composition.sealed(premise)
    raw = build.BBC / "cpu-nmi-idle-summary/positive/registers.json"
    files = [
        Path(__file__),
        Path(enable_composition.__file__),
        premise,
        raw,
        ROOT / "rtl/bbc/BbcCpu.v",
        build.BBC / "ArletCpuEnabled.v",
    ]
    before = build.hashes(files)
    graph = json.loads(raw.read_text())
    module = graph["modules"]["BbcCpu"]
    state = [
        bit
        for name, cell in sorted(module["cells"].items())
        if cell["type"] in {"$dff", "$adff"}
        for bit in cell["connections"]["Q"]
    ]
    assert len(state) == len(set(state)) == 168
    module["ports"]["proofAllState"] = {"direction": "output", "bits": state}
    module["netnames"]["proofAllState"] = {"hide_name": 0, "bits": state, "attributes": {}}
    graph["modules"] = {"ResetCpu": module}
    observed = directory / "observed.json"
    observed.write_text(json.dumps(graph))
    contract = directory / "contract.v"
    contract.write_text("""module ResetReadBridge(input clk, output equivalent);
reg [3:0] resetEdges=0,age=0;
wire reset=resetEdges<8;
(* anyseq *)reg [7:0] dummyRead,liveRead;
(* anyseq *)reg irq,nmi,warm;
always @(posedge clk)begin
 if(reset)begin resetEdges<=resetEdges+1;age<=0;end
 else if(age<12)age<=age+1;
end
wire [7:0] referenceRead=age<5 ? 8'hea : liveRead;
wire [7:0] actualRead=age<5 ? dummyRead : liveRead;
wire [167:0] referenceState,actualState;
wire [15:0] referenceAddress,actualAddress;
wire [7:0] referenceData,actualData;
wire referenceWrite,actualWrite,referenceSync,actualSync;
ResetCpu reference(.clk(clk),.reset(reset),.enable(1'b1),.irq(age>=7&&irq),.nmi(age>=7&&nmi),.warm_reset(age>=7&&warm),.read_data(referenceRead),.proofAllState(referenceState),.address(referenceAddress),.write_data(referenceData),.write_enable(referenceWrite),.instruction(referenceSync));
ACTUAL_CPU actual(.clk(clk),.reset(reset),.enable(1'b1),.irq(age>=7&&irq),.nmi(age>=7&&nmi),.warm_reset(age>=7&&warm),.read_data(actualRead),.proofAllState(actualState),.address(actualAddress),.write_data(actualData),.write_enable(actualWrite),.instruction(actualSync));
assign equivalent=reset || (referenceAddress==actualAddress && referenceWrite==actualWrite && referenceSync==actualSync && (!actualWrite || referenceData==actualData) && (age<7 || referenceState==actualState));
endmodule
""")
    rows = []
    for name in ["positive", "lost-vector-bit"]:
        model = copy.deepcopy(graph)
        if name != "positive":
            mutated = copy.deepcopy(module)
            bit = mutated["ports"]["read_data"]["bits"][0]
            for cell in mutated["cells"].values():
                for port, connection in cell["connections"].items():
                    if cell["port_directions"][port] == "input":
                        cell["connections"][port] = ["0" if b == bit else b for b in connection]
            model["modules"]["ResetCpuFault"] = mutated
        json_path = directory / (name + ".json")
        json_path.write_text(json.dumps(model))
        source = directory / (name + ".v")
        source.write_text(
            contract.read_text().replace(
                "ACTUAL_CPU", "ResetCpu" if name == "positive" else "ResetCpuFault"
            )
        )
        log = directory / (name + ".log")
        witness = directory / (name + "-counterexample.json")
        command = f"read_json {json_path}; read_verilog -formal {source}; prep -top ResetReadBridge -flatten; check -assert; async2sync; opt_clean; sat -set-init-zero -tempinduct -initsteps 20 -maxsteps 28 -prove equivalent 1 -verify -timeout 120 -show age -dump_json {witness}"
        if name != "positive":
            command = (
                command[: command.index("; sat ")]
                + f"; sat -set-init-zero -seq 24 -prove equivalent 1 -verify -show age -dump_json {witness}"
            )
        with log.open("w") as out:
            result = subprocess.run(
                ["yosys", "-Q", "-T", "-p", command],
                cwd=ROOT,
                stdout=out,
                stderr=subprocess.STDOUT,
                timeout=600,
            )
        text = log.read_text()
        accepted = (
            (result.returncode == 0 and "Induction step proven: SUCCESS!" in text)
            if name == "positive"
            else (
                result.returncode != 0
                and "SAT proof finished - model found: FAIL!" in text
                and witness.is_file()
            )
        )
        if not accepted:
            raise ValueError("Reset read bridge failed: " + name)
        rows.append({"name": name, "accepted": accepted})
    stable = before == build.hashes(files)
    report = {
        "passed": stable,
        "inputs": before,
        "inputs_unchanged": stable,
        "artifacts": build.hashes(
            [p for p in directory.iterdir() if p.is_file() and p.name != "result.json"]
        ),
        "register_bits": 168,
        "excluded_register_bits": 0,
        "proofs": rows,
        "tools": {"yosys": subprocess.check_output(["yosys", "-V"], text=True).strip()},
        "scope": "Actual CPU netlist, read-only full physical-state observation. Real reset and cold bus sequence: arbitrary bytes replace the fixed EA dummy/stack-read responses before the two shared vector bytes. Bus requests agree during entry and all 168 state bits agree afterward, with arbitrary subsequent read bytes, IRQ/NMI and warm requests. No instruction assumption or DUT state injection.",
        "limits": "Enable-one cold bridge; inactive interrupt/restart requests through vector entry, zero simulation power-on profile. Subsequent cold reset inactive. Physical wait, memory and parent/device/video composition require their separate premises.",
        "universal_game_equivalence": False,
    }
    write_json(directory / "result.json", report)
    if not stable:
        raise ValueError("Reset bridge sources changed")
    return report


if __name__ == "__main__":
    qualify()
