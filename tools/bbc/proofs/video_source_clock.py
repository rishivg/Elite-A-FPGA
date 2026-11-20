"""Bind the original video slot parity to both generated character clocks."""

import json
import re
import shutil
import subprocess
from pathlib import Path

from tools.bbc import bus_binding_proof
from tools.bbc.proofs import current_machine
from tools.bbc.proofs.cpu_reference import prepared
from tools.bbc.proofs.cpu_reference.warm_checks import accepted_result
from tools.bbc.proofs.video_cursor import hashes, replace_once
from tools.validation.common import ROOT, source_root, write_json

DIRECTORY = ROOT / "build/bbc/video-source-clock"


def guard_source(text):
    statements = (
        "this.oddClock = false;",
        "this.oddClock = !this.oddClock;",
        "if (this.halfClock && !this.oddClock) {\n                continue;\n            }",
        "this.video.halfClock = !(val & 0x10);",
    )
    if any(text.count(statement) != 1 for statement in statements):
        raise ValueError("Changed original video clock statements")
    if text.index("this.oddClock = !this.oddClock;") > text.index(statements[2]):
        raise ValueError("Changed original parity/skip ordering")


def contract(multiplier):
    base = "1'b1" if multiplier == 1 else "divider==2"
    return f"""module VideoSourceClock(input clk,output equivalent);
(* anyseq *) reg writeControl,address;
(* anyseq *) reg [7:0] data;
reg fresh=1;
always @(posedge clk)fresh<=0;
reg [1:0] divider=0;
reg [3:0] slot=0;
reg odd=0;
reg [7:0] control=0;
wire base={base};
wire tick=base && slot==15;
wire expected=tick && (control[4] || !odd);
always @(posedge clk)begin
 if(fresh)begin divider<=0;slot<=0;odd<=0;control<=0;end
 else begin
  divider<=divider==2 ? 0:divider+1;
  if(base)slot<=slot+1;
  if(tick)odd<=!odd;
  if(writeControl && !address)control<=data;
 end
end
wire actual,actualBase;
wire [4:0] master;
wire [7:0] actualControl;
BbcVideo video(.clk(clk),.reset(fresh),.io_crtcWrite(1'b0),
 .io_lightPenPulse(1'b0),.io_ulaWrite(writeControl),.io_address(address),
 .io_data(data),.io_scroll(2'b0),.io_memoryData(8'b0),
 .io_control(actualControl),.proofChar(actual),.proofBase(actualBase),.proofMaster(master));
assign equivalent=fresh || (actual==expected && actualBase==base &&
 master[3:0]==slot && master[4]==!odd && actualControl==control);
endmodule
"""


def qualify():
    current_machine.audit()
    DIRECTORY.mkdir(parents=True, exist_ok=True)
    original = source_root("jsbeeb") / "src/video.js"
    guard_source(original.read_text())
    checker = Path(__file__).with_suffix(".mjs")
    parents = current_machine.parents()
    files = [
        Path(__file__),
        checker,
        original,
        ROOT / "tools/bbc/reference.mjs",
        ROOT / "rtl/bbc/BbcVideo.scala",
        Path(bus_binding_proof.__file__),
        Path(current_machine.__file__),
        Path(prepared.__file__),
        Path(__file__).with_name("video_cursor.py"),
        Path(__file__).parent / "cpu_reference/warm_checks.py",
        ROOT / "tools/validation/common.py",
        ROOT / "build/bbc/equivalence-current/generation.json",
        *[path for _, path, _ in parents],
        *[path for _, parent, _ in parents for path in parent.parent.glob("*.bin")],
        *[Path(shutil.which(tool)) for tool in ("node", "yosys")],
    ]
    before = hashes(files)
    js_result = DIRECTORY / "original-js.json"
    subprocess.run(
        ["node", str(checker), str(source_root("jsbeeb")), str(js_result)], check=True, timeout=60
    )
    checks, artifacts = [], [js_result]
    for multiplier, parent, _ in parents:
        original_video = bus_binding_proof.module(parent.read_text(), "BbcVideo")
        video = replace_once(
            original_video,
            "module BbcVideo (",
            "module BbcVideo (output proofChar,output proofBase,output [4:0] proofMaster,",
        )
        video = replace_once(
            video,
            "endmodule",
            "assign proofChar=crtc_io_enable;assign proofBase=clock32;assign proofMaster=master;\nendmodule",
        )
        for name, body, correct in (
            ("positive", video, True),
            ("wrong-first-slot", replace_once(video, "master <= 5'h10;", "master <= 5'h0;"), False),
            (
                "lost-fast-clock",
                replace_once(video, "assign endChar = (control[4] ?", "assign endChar = (1'b0 ?"),
                False,
            ),
        ):
            case = DIRECTORY / f"{multiplier}-{name}"
            case.mkdir(exist_ok=True)
            actual, prop, graph, log, witness = [
                case / filename
                for filename in (
                    "actual.v",
                    "contract.v",
                    "graph.json",
                    "proof.log",
                    "witness.json",
                )
            ]
            # Retain actual children. Only the observed video module is replaced;
            # unused rendering logic is removed by Yosys after elaboration.
            actual.write_text(
                re.sub(
                    r'\$readmemb\("([^\"]+)"',
                    lambda match: '$readmemb("' + str(parent.parent / match[1]) + '"',
                    replace_once(parent.read_text(), original_video, body),
                )
            )
            prop.write_text(contract(multiplier))
            prepared.prepare(
                case,
                f"read_verilog -formal -sv {actual} {prop}; prep -top VideoSourceClock -flatten; check -assert; async2sync; opt; memory_map; opt; write_json {graph}",
                [actual, prop],
                [graph],
            )
            module = json.loads(graph.read_text())["modules"]["VideoSourceClock"]
            if any(cell["type"] in ("$assume", "$check") for cell in module["cells"].values()):
                raise ValueError("Video source clock acquired an assumption")
            mode = "-tempinduct -initsteps 2 -maxsteps 8" if correct else "-seq 128"
            witness.unlink(missing_ok=True)
            with log.open("w") as output:
                trial = subprocess.run(
                    [
                        "yosys",
                        "-Q",
                        "-T",
                        "-p",
                        f"read_json {graph}; sat -set-init-zero {mode} -prove equivalent 1 -verify -timeout 120 -show equivalent -dump_json {witness}",
                    ],
                    stdout=output,
                    stderr=subprocess.STDOUT,
                    timeout=240,
                )
            accepted = (
                trial.returncode == 0 and "Induction step proven: SUCCESS!" in log.read_text()
                if correct
                else accepted_result(
                    trial.returncode, log.read_text(), witness, "equivalent", False
                )
            )
            checks.append({"clock_multiplier": multiplier, "name": name, "accepted": accepted})
            artifacts.extend([p for p in case.glob("*") if p.is_file()])
            print(
                f"Video source clock {multiplier}-{name}: " + ("PASS" if accepted else "FAIL"),
                flush=True,
            )
            if not accepted:
                raise ValueError("Video source clock comparison incomplete")
    current_machine.audit()
    stable = before == hashes(files)
    passed = stable and len(checks) == 6 and json.loads(js_result.read_text())["passed"]
    write_json(
        DIRECTORY / "result.json",
        {
            "passed": passed,
            "inputs_unchanged": stable,
            "inputs": before,
            "artifacts": hashes(artifacts),
            "checks": checks,
            "scope": "Initialized unbounded 32/96 MHz character-clock and slot-parity equivalence to guarded original Video.polltime statements under the declared ULA-control-zero shared-reset profile. Arbitrary control writes, including coincident slot boundaries. Native original JS cases check parity, skip and control-write ordering; wrong first slot and lost fast clock are detected at both rates.",
            "limits": "Video component constructor/reset clock domain. Parent epoch and CPU/device callback ordering, original reset API, rendering and HSYNC width-zero eligibility remain separate composition obligations.",
            "universal_game_equivalence": False,
        },
    )
    if not passed:
        raise ValueError("Video source clock inputs changed")


if __name__ == "__main__":
    qualify()
