"""Compare CRTC horizontal sync with the original JS character-domain behavior."""

import shutil
import subprocess
from pathlib import Path

from tools.bbc import bus_binding_proof
from tools.bbc.proofs import current_machine
from tools.bbc.proofs.cpu_reference import prepared
from tools.bbc.proofs.cpu_reference.warm_checks import accepted_result
from tools.bbc.proofs.video_cursor import hashes, replace_once
from tools.validation.common import ROOT, source_root, write_json

DIRECTORY = ROOT / "build/bbc/hsync-source-history"


def source_guard(text):
    fragments = (
        "this.video.hpulseWidth = val & 0x0f;",
        "this.hpulseWidth = 0;",
        "if (this.inHSync && this.handleHSync()) this.flyback();",
        "this.hpulseCounter = (this.hpulseCounter + 1) & 0x0f;",
        "if (this.hpulseCounter === this.hpulseWidth >>> 1)",
        "} else if (this.hpulseCounter === (this.regs[3] & 0x0f)) {\n            this.inHSync = false;",
        "if (this.horizCounter === this.regs[2] && !this.inHSync) {\n                this.inHSync = true;\n                this.hpulseCounter = 0;",
    )
    if any(text.count(fragment) != 1 for fragment in fragments):
        raise ValueError("Changed original horizontal-sync statements")
    if text.index("if (this.inHSync && this.handleHSync())") > text.index(
        "if (this.horizCounter === this.regs[2] && !this.inHSync)"
    ):
        raise ValueError("Changed original sync termination/initiation order")


def contract(nonzero, *, blank_startup=False):
    width = "(rawWidth==0 ? 4'd1:rawWidth)" if nonzero else "rawWidth"
    profile = "wire [143:0] visibleRegisters=rawRegisters;"
    extra = ""
    if blank_startup:
        width = "(configured && rawWidth==0 ? 4'd1:rawWidth)"
        profile = """reg configured=0;
always @(posedge clk or posedge reset)
 if(reset)configured<=0;
 else if(rawWidth!=0)configured<=1;
wire [143:0] visibleRegisters=configured ? rawRegisters : (rawRegisters & ~144'hff00ff);"""
        extra = " && (configured || horizontal==0)"
    return f"""module HsyncSourceHistory(input clk,output equivalent);
(* anyseq *) reg resetRequest,enable;
(* anyseq *) reg [143:0] rawRegisters;
reg fresh=1;
always @(posedge clk)fresh<=0;
wire reset=fresh || resetRequest;
wire [3:0] rawWidth=rawRegisters[27:24];
{profile}
wire [3:0] width={width};
wire [143:0] registers=(visibleRegisters&~(144'hf<<24))|({{140'd0,width}}<<24);
wire [4:0] actual;
wire [7:0] horizontal;
Crtc6845 dut(.clk(clk),.reset(reset),.io_enable(enable),.io_registers(registers),.proofSync(actual),.proofHorizontal(horizontal));
reg active=0;
reg [3:0] count=0;
wire [3:0] increment=count+4'd1;
wire ending=active && increment!=(width>>1) && increment==width;
wire start=horizontal==registers[23:16] && (!active || ending);
always @(posedge clk or posedge reset)
 if(reset)begin active<=0;count<=0;end
 else if(enable)begin
  if(active)begin count<=increment;if(ending)active<=0;end
  if(start)begin active<=1;count<=0;end
 end
assign equivalent=reset || (actual=={{active,count}}{extra});
endmodule
"""


def qualify():
    DIRECTORY.mkdir(parents=True, exist_ok=True)
    current_machine.audit()
    original = source_root("jsbeeb")
    native = Path(__file__).with_name("hsync_source_reference.mjs")
    files = [
        Path(__file__),
        native,
        Path(prepared.__file__),
        Path(current_machine.__file__),
        Path(bus_binding_proof.__file__),
        Path(__file__).with_name("video_cursor.py"),
        ROOT / "tools/validation/common.py",
        ROOT / "rtl/bbc/Crtc6845.scala",
        ROOT / "build/bbc/equivalence-current/generation.json",
        *sorted((original / "src").rglob("*.js")),
        *[path for _, path, _ in current_machine.parents()],
        *[Path(shutil.which(tool)) for tool in ("node", "yosys")],
    ]
    before = hashes(files)
    source_guard((original / "src/video.js").read_text())
    subprocess.run(
        ["node", str(native), str(original), str(DIRECTORY / "native.json")], check=True, timeout=60
    )
    observed = [
        bus_binding_proof.module(path.read_text(), "Crtc6845")
        for _, path, _ in current_machine.parents()
    ]
    if observed[0] != observed[1]:
        raise ValueError("Current parents have different CRTC children")
    child = replace_once(
        observed[0],
        "module Crtc6845 (",
        "module Crtc6845 (output wire [4:0] proofSync,output wire [7:0] proofHorizontal,",
    )
    child = replace_once(
        child,
        "endmodule",
        "assign proofSync={hsync,hcount};assign proofHorizontal=horizontal;\nendmodule",
    )
    dut = DIRECTORY / "dut.v"
    dut.write_text(child)
    checks = []
    for name, nonzero, startup in (
        ("nonzero-width", True, False),
        ("blank-zero-startup", False, True),
        ("zero-width-diagnostic", False, False),
    ):
        case = DIRECTORY / name
        case.mkdir(exist_ok=True)
        property_file, graph, log, witness = [
            case / filename
            for filename in ("contract.v", "graph.json", "proof.log", "witness.json")
        ]
        property_file.write_text(contract(nonzero, blank_startup=startup))
        prepared.prepare(
            case,
            f"read_verilog -formal -sv {dut} {property_file}; prep -top HsyncSourceHistory -flatten; check -assert; opt; memory_map; opt; async2sync; dffunmap; opt_clean; write_json {graph}",
            [dut, property_file],
            [graph],
        )
        witness.unlink(missing_ok=True)
        positive = nonzero or startup
        mode = "-tempinduct -initsteps 4 -maxsteps 8" if positive else "-seq 20"
        with log.open("w") as output:
            trial = subprocess.run(
                [
                    "yosys",
                    "-Q",
                    "-T",
                    "-p",
                    f"read_json {graph}; sat -set-init-zero {mode} -prove equivalent 1 -verify -timeout 120 -show equivalent -show actual -show active -show count -show width -show horizontal -dump_json {witness}",
                ],
                stdout=output,
                stderr=subprocess.STDOUT,
                timeout=600,
            )
        accepted = (
            (trial.returncode == 0 and "Induction step proven: SUCCESS!" in log.read_text())
            if positive
            else accepted_result(trial.returncode, log.read_text(), witness, "equivalent", False)
        )
        checks.append(
            {
                "name": name,
                "accepted": accepted,
                "equivalence_passed": positive and accepted,
                "counterexample_found": not positive and accepted,
            }
        )
        verdict = "PASS" if positive else "DIFFERENCE CONFIRMED"
        print("Horizontal sync " + name + (": " + verdict if accepted else ": FAIL"), flush=True)
    stable = before == hashes(files)
    current_machine.audit()
    write_json(
        DIRECTORY / "result.json",
        {
            "passed": stable and all(check["accepted"] for check in checks),
            "all_widths_equivalent": False,
            "inputs_unchanged": stable,
            "inputs": before,
            "artifacts": hashes(
                [
                    dut,
                    DIRECTORY / "native.json",
                    *[
                        path
                        for check in checks
                        for path in (DIRECTORY / check["name"]).glob("*")
                        if path.is_file()
                    ],
                ]
            ),
            "checks": checks,
            "scope": "Initialized unbounded character-domain horizontal-sync/counter comparison for arbitrary enables, resets, horizontal register bytes and pulse widths 1 through 15. A second property admits zero width during blank startup (R0=R2=0 until the first nonzero R3 width, and nonzero thereafter). Source lowering exhaustively checked against original JS handleHSync for all 1024 finite state/width/initiation combinations. A separate unrestricted diagnostic must retain a real zero-width counterexample.",
            "limits": "Outside the blank startup profile a width-zero wrap can diverge. The original JS takes its beam-centering branch instead of clearing sync; RTL ends the old pulse on the wrap. The reachable register history, renderer/beam model and silicon behavior retain separate obligations.",
            "universal_game_equivalence": False,
        },
    )
    if not stable or not all(check["accepted"] for check in checks):
        raise ValueError("Horizontal-sync comparison incomplete")


if __name__ == "__main__":
    qualify()
