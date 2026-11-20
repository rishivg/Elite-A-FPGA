"""Prove that parent 2 MHz slots and video character phase share one clock."""

import json
import re
import shutil
import subprocess
from pathlib import Path

from tools.bbc import bus_binding_proof
from tools.bbc.proofs import current_machine, memory_composition, video_source_clock
from tools.bbc.proofs.cpu_reference import prepared
from tools.bbc.proofs.video_cursor import hashes, replace_once
from tools.validation.common import ROOT, write_json

DIRECTORY = ROOT / "build/bbc/video-parent-phase"


def qualify():
    current_machine.audit()
    premise = video_source_clock.DIRECTORY / "result.json"
    memory_composition.sealed(premise)
    DIRECTORY.mkdir(parents=True, exist_ok=True)
    parents = current_machine.parents()
    cpu = ROOT / "rtl/bbc/BbcCpu.v"
    core, alu = ROOT / "build/bbc/ArletCpuEnabled.v", ROOT / "third_party/6502/ALU.v"
    files = [
        Path(__file__),
        premise,
        cpu,
        core,
        alu,
        Path(bus_binding_proof.__file__),
        Path(current_machine.__file__),
        Path(memory_composition.__file__),
        Path(prepared.__file__),
        Path(video_source_clock.__file__),
        Path(__file__).with_name("video_cursor.py"),
        ROOT / "tools/validation/common.py",
        ROOT / "build/bbc/equivalence-current/generation.json",
        *[path for _, path, _ in parents],
        *[path for _, parent, _ in parents for path in parent.parent.glob("*.bin")],
        Path(shutil.which("yosys")),
    ]
    before = hashes(files)
    checks, artifacts = [], []
    for multiplier, source, top in parents:
        parent = bus_binding_proof.module(source.read_text(), top)
        video = bus_binding_proof.module(source.read_text(), "BbcVideo")
        bindings = bus_binding_proof.ports(parent, "BbcVideo", "video")
        if bindings.get("clk") != "clk" or bindings.get("reset") != "reset":
            raise ValueError("Video clock/reset differ from actual parent")
        phase_match = re.search(
            r"assign tickCpu = \(clock32 && \((\w+)\[3 : 0\] == 4'b1111\)\);", parent
        )
        if phase_match is None:
            raise ValueError("Changed actual parent 2 MHz phase expression")
        phase_name = phase_match[1]
        observed_video = replace_once(
            video,
            "module BbcVideo (",
            "module BbcVideo (output [4:0] proofMaster,output proofBase,",
        )
        observed_video = replace_once(
            observed_video,
            "endmodule",
            "assign proofMaster=master;assign proofBase=clock32;\nendmodule",
        )
        observed_parent = replace_once(
            parent,
            f"module {top} (",
            f"module {top} (output [4:0] proofPhase,output [4:0] proofVideoMaster,output proofBase,output proofVideoBase,output proofTick,",
        )
        observed_parent = replace_once(
            observed_parent,
            "BbcVideo video (",
            "BbcVideo video (.proofMaster(proofVideoMaster),.proofBase(proofVideoBase),",
        )
        observed_parent = replace_once(
            observed_parent,
            "endmodule",
            f"assign proofPhase={phase_name};assign proofBase=clock32;assign proofTick=tickCpu;\nendmodule",
        )
        actual = replace_once(
            replace_once(source.read_text(), video, observed_video), parent, observed_parent
        )
        actual = re.sub(
            r'\$readmemb\("([^\"]+)"',
            lambda match: '$readmemb("' + str(source.parent / match[1]) + '"',
            actual,
        )
        case = DIRECTORY / str(multiplier)
        case.mkdir(exist_ok=True)
        model, prop, graph, log = [
            case / filename for filename in ("actual.v", "contract.v", "graph.json", "proof.log")
        ]
        model.write_text(actual)
        inputs = re.findall(r"input\s+wire\s+(\[[^]]+\]\s*)?(\w+)", parent.split(");", 1)[0])
        symbolic = "\n".join(
            f"(* anyseq *) reg {width}source_{name};"
            for width, name in inputs
            if name not in ("clk", "reset")
        )
        connections = ",".join(
            f".{name}(source_{name})" for _, name in inputs if name not in ("clk", "reset")
        )
        prop.write_text(f"""module VideoParentPhase(input clk,output equivalent);
{symbolic}
reg fresh=1;
always @(posedge clk)fresh<=0;
wire [4:0] phase,master;
wire base,videoBase,tick;
{top} parent({connections},.clk(clk),.reset(fresh),.proofPhase(phase),.proofVideoMaster(master),
 .proofBase(base),.proofVideoBase(videoBase),.proofTick(tick));
assign equivalent=fresh || (master==(phase+5'd16) && base==videoBase &&
 tick==(videoBase && master[3:0]==15));
endmodule
""")
        prepared.prepare(
            case,
            f"read_verilog -formal -sv {model} {cpu} {core} {alu} {prop}; prep -top VideoParentPhase -flatten; check -assert; chformal -assert -remove; opt_clean -purge; async2sync; opt; memory_map; opt; write_json {graph}",
            [model, cpu, core, alu, prop],
            [graph],
        )
        module = json.loads(graph.read_text())["modules"]["VideoParentPhase"]
        if any(cell["type"] in ("$assume", "$check") for cell in module["cells"].values()):
            raise ValueError("Parent video phase acquired an assumption")
        with log.open("w") as output:
            trial = subprocess.run(
                [
                    "yosys",
                    "-Q",
                    "-T",
                    "-p",
                    f"read_json {graph}; sat -set-init-zero -tempinduct -initsteps 2 -maxsteps 8 -prove equivalent 1 -verify -timeout 120",
                ],
                stdout=output,
                stderr=subprocess.STDOUT,
                timeout=240,
            )
        accepted = trial.returncode == 0 and "Induction step proven: SUCCESS!" in log.read_text()
        checks.append({"clock_multiplier": multiplier, "accepted": accepted})
        artifacts.extend([p for p in case.glob("*") if p.is_file()])
        print(f"Parent video phase {multiplier}: " + ("PASS" if accepted else "FAIL"), flush=True)
        if not accepted:
            break
    memory_composition.sealed(premise)
    current_machine.audit()
    stable = before == hashes(files)
    passed = stable and len(checks) == 2 and all(check["accepted"] for check in checks)
    write_json(
        DIRECTORY / "result.json",
        {
            "passed": passed,
            "inputs_unchanged": stable,
            "inputs": before,
            "artifacts": hashes(artifacts),
            "checks": checks,
            "scope": "Initialized unbounded actual 32/96 MHz parent/video phase identity. Every parent 2 MHz slot is exactly a video half-character boundary; their 32 MHz clock enables coincide and video master equals parent phase plus16 modulo32. No input assumptions or executing state changes; only read-only probe ports are added.",
            "limits": "Connects the component source-clock theorem to parent slot boundaries. Original absolute epoch, device callback ordering, video rendering and whole-game composition remain separate.",
            "universal_game_equivalence": False,
        },
    )
    if not passed:
        raise ValueError("Parent video phase comparison incomplete")


if __name__ == "__main__":
    qualify()
