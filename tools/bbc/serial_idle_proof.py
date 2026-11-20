"""Prove that serial state cannot change between enabled digital endpoints."""

import re
import subprocess
from pathlib import Path

from tools.validation.common import write_json

from . import build, bus_decode


def observed_module(text):
    states = {
        "control": 8,
        "baud": 3,
        "rs423": 1,
        "empty": 1,
        "pending": 1,
        "remaining": 19,
        "transmitPeriod": 19,
    }
    for name, width in states.items():
        dimension = rf"\[{width - 1}:0\]\s+" if width > 1 else ""
        if len(re.findall(rf"reg\s+{dimension}{name}\s*;", text)) != 1:
            raise ValueError("Changed serial state inventory: " + name)
    written = set(re.findall(r"\b(\w+)(?:\[[^\]]+\])?\s*<=", text))
    if written != set(states):
        raise ValueError("Unobserved or missing serial sequential state")
    header = text.index(");")
    ports = ", output wire [51:0] proofState\n"
    assignments = "assign proofState = {" + ",".join(states) + "};\n"
    return (
        text[:header]
        + ports
        + text[header : text.rindex("endmodule")]
        + assignments
        + "endmodule\n"
    )


CONTRACT = """module SerialIdleContract(
input clk, reset, tick, aciaRead, aciaWrite, serialRead, serialWrite, address,
input [7:0] data, output stable);
wire [51:0] state;
BbcSerial actual(.clk(clk), .reset(reset), .io_tick(tick),
.io_aciaRead(aciaRead), .io_aciaWrite(aciaWrite), .io_serialRead(serialRead),
.io_serialWrite(serialWrite), .io_address(address), .io_data(data),
.proofState(state));
reg [51:0] previous;
reg idle;
always @(posedge clk) begin
previous <= state;
idle <= !reset && !tick && !aciaWrite && !serialRead && !serialWrite;
end
assign stable = reset || !idle || state == previous;
endmodule
"""


def qualify():
    generation = bus_decode.elaborate()
    source = bus_decode.DIRECTORY / "BbcSerial.v"
    before = build.hashes([source, Path(__file__)])
    directory = build.BBC / "serial-idle-proof"
    directory.mkdir(exist_ok=True)
    actual = directory / "actual.v"
    actual.write_text(observed_module(source.read_text()))
    contract = directory / "contract.v"
    contract.write_text(CONTRACT)

    def prove(model, log):
        command = (
            f"read_verilog -formal {model} {contract}; hierarchy -top SerialIdleContract; "
            "proc; flatten; async2sync; opt_clean; setattr -unset init w:*; "
            "sat -seq 2 -prove-skip 1 -set-init-def -set-def-inputs -prove stable 1 -verify"
        )
        with log.open("w") as output:
            result = subprocess.run(
                ["yosys", "-Q", "-T", "-p", command],
                stdout=output,
                stderr=subprocess.STDOUT,
                timeout=60,
            )
        return result.returncode, log.read_text()

    code, log = prove(actual, directory / "proof.log")
    # Alter the real timer gate, rather than the observation contract.
    text = actual.read_text()
    target = re.search(r"if\((when_BbcSerial_l\d+)\) begin\n\s+remaining <=", text)
    if target is None:
        raise ValueError("Missing serial idle negative target")
    negative = directory / "wrong-idle-update.v"
    negative.write_text(
        text[: target.start()]
        + text[target.start() :].replace(target[0], target[0].replace(target[1], "1'b1"), 1)
    )
    bad_code, bad_log = prove(negative, directory / "negative.log")
    detected = bad_code != 0 and "proof did fail" in bad_log
    unchanged = generation == bus_decode.elaborate() and before == build.hashes(
        [source, Path(__file__)]
    )
    report = {
        "passed": code == 0 and "SUCCESS" in log and detected and unchanged,
        "inputs": before,
        "inputs_unchanged": unchanged,
        "negative_control_detected": detected,
        "artifacts": build.hashes(
            [actual, contract, negative, directory / "proof.log", directory / "negative.log"]
        ),
        "scope": "All 52 actual serial state bits are unchanged on an idle edge, for arbitrary prior state and inputs. Initial attributes are removed; only clock/reset and accepted-event semantics are used. A deliberately ungated timer must fail.",
        "limits": "Permits omitting idle master edges in the standalone serial probe with unchanged external inputs. Does not permit skipping machine edges or accepted serial endpoints.",
    }
    write_json(directory / "result.json", report)
    print(f"Serial idle-state proof: {report['passed']}", flush=True)
    if not report["passed"]:
        raise ValueError("Serial idle-state proof failed")
    return report


if __name__ == "__main__":
    qualify()
