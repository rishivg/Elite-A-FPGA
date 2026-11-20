"""Compare current CRTC histories with a mechanically lowered JS projection."""

import json
import shutil
import subprocess
from pathlib import Path

from tools.bbc import bus_binding_proof
from tools.bbc.proofs import current_machine, memory_composition
from tools.bbc.proofs.cpu_reference import prepared
from tools.bbc.proofs.cpu_reference.warm_checks import accepted_result
from tools.bbc.proofs.video_cursor import hashes, replace_once
from tools.validation.common import ROOT, digest, source_root, write_json

DIRECTORY = ROOT / "build/bbc/crtc-source-history"


def contract(fields, width, initial):
    offset = width
    locations = {}
    for name, bits, _ in fields:
        offset -= bits
        locations[name] = (offset + bits - 1, offset)
    count_low = locations["frameCount"][1]
    dummy = locations["endOfFrameLatched"][1]
    adjust_end = locations["endOfVertAdjustLatched"][1]
    adjusting = locations["inVertAdjust"][1]
    return f"""module CrtcSourceHistory(input clk,output equivalent,output observable);
(* anyseq *) reg resetRequest,enable;
(* anyseq *) reg [143:0] registers;
reg fresh=1;
always @(posedge clk)fresh<=0;
wire reset=fresh || resetRequest;
wire [{width - 1}:0] actual,following;
wire actualParity,actualFrame,sourceFrame;
reg [{width - 1}:0] sourceState={width}'h{initial};
CrtcSourceNext original(.state(sourceState),.registers(registers[111:0]),.nextState(following),.frame(sourceFrame));
always @(posedge clk or posedge reset)
 if(reset)sourceState<={width}'h{initial};
 else if(enable)sourceState<=following;
Crtc6845 actualCrtc(.clk(clk),.reset(reset),.io_enable(enable),.io_registers(registers),
 .proofState(actual),.proofParity(actualParity),.io_frame(actualFrame));
wire dummyShape=!sourceState[{dummy}] || (sourceState[{adjust_end}] && !sourceState[{adjusting}]);
assign observable=reset || (actual==sourceState && actualFrame==(enable && sourceFrame));
assign equivalent=reset || (dummyShape && actualParity==sourceState[{count_low}] && observable);
endmodule
"""


def native_check(source, fixtures):
    directory = DIRECTORY / "native"
    directory.mkdir(exist_ok=True)
    driver = ROOT / "sim/native/crtc_source_projection_test.cpp"
    binary = directory / "VCrtcSourceNext"
    command = [
        "verilator",
        "--cc",
        "--exe",
        "--build",
        "-j",
        "1",
        "-Wno-fatal",
        "--top-module",
        "CrtcSourceNext",
        "--Mdir",
        str(directory),
        "-CFLAGS",
        "-O3 -std=c++17",
        str(source),
        str(driver),
    ]
    inputs = {
        "command": command,
        "files": hashes(
            [source, driver, Path(shutil.which("verilator")), Path(shutil.which("c++"))]
        ),
    }
    receipt = directory / "build.json"
    saved = json.loads(receipt.read_text()) if receipt.exists() else {}
    if (
        saved.get("inputs") != inputs
        or not binary.is_file()
        or saved.get("binary") != digest(binary)
    ):
        with (DIRECTORY / "native-build.log").open("w") as output:
            subprocess.run(
                command, stdout=output, stderr=subprocess.STDOUT, check=True, timeout=180
            )
        write_json(receipt, {"inputs": inputs, "binary": digest(binary)})
    trial = subprocess.run([str(binary), str(fixtures)], capture_output=True, text=True, timeout=60)
    (DIRECTORY / "native.log").write_text(trial.stdout + trial.stderr)
    if (
        trial.returncode
        or trial.stderr
        or trial.stdout != "Original CRTC projection: 65536 fixtures; 0 differences\n"
    ):
        raise ValueError("Lowered CRTC projection differs from independently executed JS")


def qualify():
    DIRECTORY.mkdir(parents=True, exist_ok=True)
    current_machine.generate()
    original = source_root("jsbeeb")
    producer = ROOT / "tools/bbc/crtc_source_projection.mjs"
    files = [
        Path(__file__),
        producer,
        ROOT / "sim/native/crtc_source_projection_test.cpp",
        Path(current_machine.__file__),
        Path(bus_binding_proof.__file__),
        Path(memory_composition.__file__),
        Path(prepared.__file__),
        Path(__file__).parent / "cpu_reference/warm_checks.py",
        Path(__file__).with_name("video_cursor.py"),
        ROOT / "tools/validation/common.py",
        ROOT / "tools/bbc/build.py",
        ROOT / "rtl/bbc/Crtc6845.scala",
        ROOT / "package-lock.json",
        *sorted((ROOT / "node_modules/prettier/plugins").glob("*.mjs")),
        *sorted((original / "src").rglob("*.js")),
        *[p for _, p, _ in current_machine.parents()],
        *[Path(shutil.which(tool)) for tool in ("node", "yosys", "c++", "verilator")],
    ]
    before = hashes(files)
    subprocess.run(["node", str(producer), str(original), str(DIRECTORY)], check=True, timeout=90)
    projection = json.loads((DIRECTORY / "projection.json").read_text())
    fields, width = projection["fields"], projection["width"]
    if width != 90 or len(fields) != 23:
        raise ValueError("Changed CRTC projection domain")
    native_check(DIRECTORY / "source.v", DIRECTORY / "fixtures.tsv")
    initial = json.loads((DIRECTORY / "initial.json").read_text())["state"]
    property_file = DIRECTORY / "contract.v"
    property_file.write_text(contract(fields, width, initial))
    children = [
        bus_binding_proof.module(path.read_text(), "Crtc6845")
        for _, path, _ in current_machine.parents()
    ]
    for _, path, _ in current_machine.parents():
        video = bus_binding_proof.module(path.read_text(), "BbcVideo")
        ports = bus_binding_proof.ports(video, "Crtc6845", "crtc")
        if (
            any(
                ports.get(port, "").strip() != signal
                for port, signal in (
                    ("clk", "clk"),
                    ("reset", "reset"),
                    ("io_enable", "crtc_io_enable"),
                    ("io_registers", "crtc_io_registers[143:0]"),
                )
            )
            or video.count("assign crtc_io_enable = (clock32 && endChar);") != 1
        ):
            raise ValueError("Changed current CRTC clock/register binding")
    if children[0] != children[1]:
        raise ValueError("Different current CRTC children at the two clock rates")
    observed = replace_once(
        children[0],
        "module Crtc6845 (",
        f"module Crtc6845 (output wire [{width - 1}:0] proofState,output wire proofParity,",
    )
    observed = replace_once(
        observed,
        "endmodule",
        "assign proofState={"
        + ",".join(field[2] for field in fields)
        + "};\nassign proofParity=frameParity;\nendmodule",
    )
    variants = [
        ("positive", observed),
        (
            "lost-address-advance",
            replace_once(observed, "nextAddress = (address + 14'h0001);", "nextAddress = address;"),
        ),
        ("lost-cursor-start", replace_once(observed, "cursorOn <= 1'b1;", "cursorOn <= 1'b0;")),
        (
            "lost-field-parity",
            replace_once(
                observed, "frameParity <= (! frameParity);", "frameParity <= frameParity;"
            ),
        ),
    ]
    checks = []
    artifacts = [
        DIRECTORY / "source.v",
        DIRECTORY / "fixtures.tsv",
        DIRECTORY / "projection.json",
        DIRECTORY / "initial.json",
        DIRECTORY / "native-build.log",
        DIRECTORY / "native.log",
        DIRECTORY / "native/VCrtcSourceNext",
        DIRECTORY / "native/build.json",
        property_file,
    ]
    for name, text in variants:
        case = DIRECTORY / name
        case.mkdir(exist_ok=True)
        actual, graph, log, witness = [
            case / filename for filename in ("actual.v", "graph.json", "proof.log", "witness.json")
        ]
        actual.write_text(text)
        source = DIRECTORY / "source.v"
        command = f"read_verilog -formal -sv {source} {actual} {property_file}; prep -top CrtcSourceHistory -flatten; check -assert; async2sync; opt; write_json {graph}"
        cached = prepared.prepare(case, command, [source, actual, property_file], [graph])
        if any(
            cell["type"] in ("$assume", "$check")
            for cell in json.loads(graph.read_text())["modules"]["CrtcSourceHistory"][
                "cells"
            ].values()
        ):
            raise ValueError("CRTC source-history property acquired an assumption")
        positive = name == "positive"
        property_name = "equivalent" if positive else "observable"
        mode = "-tempinduct -initsteps 3 -maxsteps 12" if positive else "-seq 12"
        witness.unlink(missing_ok=True)
        with log.open("w") as output:
            trial = subprocess.run(
                [
                    "yosys",
                    "-Q",
                    "-T",
                    "-p",
                    f"read_json {graph}; sat -set-init-zero {mode} -prove {property_name} 1 -verify -timeout 120 -show {property_name} -show actual -show sourceState -show registers -dump_json {witness}",
                ],
                stdout=output,
                stderr=subprocess.STDOUT,
                timeout=900,
            )
        accepted = (
            (trial.returncode == 0 and "Induction step proven: SUCCESS!" in log.read_text())
            if positive
            else accepted_result(trial.returncode, log.read_text(), witness, property_name, False)
        )
        checks.append({"name": name, "accepted": accepted, "preparation_cache_reused": cached})
        artifacts += [actual, graph, log, case / "prepare.log", case / "prepared.json"]
        if witness.is_file():
            artifacts.append(witness)
        print("CRTC source history " + name + (": PASS" if accepted else ": FAIL"), flush=True)
        if not accepted:
            break
    current_machine.audit()
    stable = before == hashes(files)
    passed = stable and len(checks) == len(variants) and all(check["accepted"] for check in checks)
    write_json(
        DIRECTORY / "result.json",
        {
            "passed": passed,
            "inputs_unchanged": stable,
            "inputs": before,
            "artifacts": hashes(artifacts),
            "checks": checks,
            "projection_bits": width,
            "original_fixtures": 65536,
            "clocks": [1, 3],
            "scope": "Initialized unbounded CRTC character histories against a mechanically lowered scalar projection of the unchanged original JS methods. All 90 projected counter/address/vertical-sync/cursor/field/display bits, field-count parity invariant and frame events compared. Arbitrary character enables, live register bytes and subsequent resets; no solver input assumptions. Current complete parents contain the identical observed CRTC. Fresh independent JS fixtures check the lowering, and three actual RTL faults must fail.",
            "limits": "Character-domain projection. JS lowering is separately checked by finite native fixtures, not a universal JS-language translation theorem. Frame count uses its low five bits in the JS exact-integer domain. Digital HSYNC, beam position, monitor rendering, source callback scheduling, register masking, global epoch and CPU/device composition remain separate. No full-game equivalence claim.",
            "universal_game_equivalence": False,
        },
    )
    if not passed:
        raise ValueError("CRTC source-history comparison incomplete")


if __name__ == "__main__":
    qualify()
