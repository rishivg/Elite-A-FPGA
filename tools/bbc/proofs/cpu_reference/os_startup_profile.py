"""Check source cold-Z normalization through the verified first OS LDA."""

import json
import shutil
import subprocess
from pathlib import Path

import pycparser

from tools.bbc.proofs import memory_composition
from tools.bbc.proofs.cpu_reference import (
    frontend,
    prepared,
    prune,
    reset_sequence,
    short_stream,
    specialized,
    structural,
)
from tools.bbc.proofs.video_cursor import hashes, replace_once
from tools.validation.common import ROOT, digest, write_json

PROFILE = """module SourceOsStartupProfile(input clk,output equivalent,output faultDefined);
(* anyseq *) reg warm;
(* anyseq *) reg [7:0] otherRead;
reg [255:0] actualState=256'hINITIAL_STATE;
reg [255:0] normalizedState=256'hNORMALIZED_STATE;
reg [63:0] actualPins=64'h43000000,normalizedPins=64'h43000000;
reg joined=0,definedHistory=1;
reg [7:0] data;
always @* case(actualPins[15:0])
 16'hfffc:data=8'hcd;
 16'hfffd:data=8'hd9;
 16'hd9cd:data=8'ha9;
 16'hd9ce:data=8'h40;
 default:data=otherRead;
endcase
wire [63:0] responseA=(actualPins&~64'hff0000)|({56'd0,data}<<16)|({63'd0,warm}<<30);
wire [63:0] responseN=(normalizedPins&~64'hff0000)|({56'd0,data}<<16)|({63'd0,warm}<<30);
wire [255:0] nextA,nextN;wire [63:0] pinsA,pinsN;wire trapA,trapN;
M6502ReferenceNext actual(.state_in(actualState),.pins_in(responseA),.state_out(nextA),.pins_out(pinsA),.trap(trapA));
M6502ReferenceNext normalized(.state_in(normalizedState),.pins_in(responseN),.state_out(nextN),.pins_out(pinsN),.trap(trapN));
wire joining=!actualPins[25] && actualState[255:240]==16'h549;
always @(posedge clk) if(!joined)begin
 actualState<=nextA;normalizedState<=nextN;actualPins<=pinsA;normalizedPins<=pinsN;
 definedHistory<=definedHistory && !trapA && !trapN;
 if(joining)joined<=1;
end
wire context=responseA[25] ? (responseA[30] || data==8'ha9) :
 actualState[255:251]==0 && (actualState[250:243]==0 || actualState[250:243]==8'ha9);
// Reset drives the status byte even on the discarded stack read. Its Z bit
// therefore also appears in read-data pins and saved PINS. Only those two
// carried read-data bits and the initial status Z may differ before retirement.
wire sameExceptZ=((actualState^normalizedState)&~((256'd1<<169)|(256'd1<<121)))==0;
wire samePins=((actualPins^normalizedPins)&~(64'd1<<17))==0;
assign equivalent=samePins && actualPins[24] && normalizedPins[24] && sameExceptZ &&
 (joined ? actualState==normalizedState && actualPins==normalizedPins :
 context && !trapA && !trapN && ((pinsA^pinsN)&~(64'd1<<17))==0 &&
 (!joining || (nextA==nextN && nextA[207:200]==8'h40 && !nextA[169])));
// Freeze only after the first LDA retires. Equality at that boundary is the
// result; later instructions and whole-machine execution are separate proofs.
assign faultDefined=!definedHistory || (!joined && (trapA || trapN)) || equivalent;
endmodule
"""


def qualify():
    paths = [
        ROOT / "build/bbc" / name / "result.json"
        for name in (
            "cpu-c-reset-sequence",
            "cpu-c-cold-entry-bridge",
            "os-startup-zero-flag-bridge",
            "rom-proof",
        )
    ]
    reset, cold, rtl_startup, rom_report = [memory_composition.sealed(p) for p in paths]
    header = ROOT / "build/bbc/reference-cycle/m6502.h"
    full = Path("/tmp/fpgaleet-c-reset-sequence/full.v")
    native = Path("/tmp/fpgaleet-c-cold-entry/initial")
    rom = ROOT / "build/bbc/rom.hex"
    for path, report, group in (
        (header, reset, "inputs"),
        (full, reset, "artifacts"),
        (native, cold, "artifacts"),
        (rom, rtl_startup, "inputs"),
    ):
        key = str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path)
        if report[group].get(key) != digest(path):
            raise ValueError("Changed source startup premise")
    image = bytes(int(x, 16) for x in rom.read_text().split())
    os_roms = [Path(name) for name in rom_report["inputs"] if name.endswith("/public/roms/os.rom")]
    if len(os_roms) != 1 or image[:16384] != os_roms[0].read_bytes():
        raise ValueError("Source and RTL startup use different verified OS ROMs")
    pc = image[0x3FFC] | image[0x3FFD] << 8
    if pc != 0xD9CD or image[pc - 0xC000 : pc - 0xC000 + 2] != bytes([0xA9, 0x40]):
        raise ValueError("Changed first OS instruction")
    inputs = [
        Path(__file__),
        *[
            Path(m.__file__)
            for m in (
                frontend,
                prepared,
                prune,
                reset_sequence,
                short_stream,
                specialized,
                structural,
                memory_composition,
            )
        ],
        Path(__file__).with_name("warm_checks.py"),
        Path(__file__).parents[1] / "video_cursor.py",
        *paths,
        header,
        full,
        native,
        rom,
        os_roms[0],
        Path(shutil.which("gcc")),
        Path(shutil.which("yosys")),
        *sorted(Path(pycparser.__file__).parent.rglob("*.py")),
    ]
    before = hashes(inputs)
    work = Path("/tmp/fpgaleet-c-source-os-startup-profile")
    work.mkdir(exist_ok=True)
    observed = subprocess.check_output([str(native)], text=True)
    values = list(map(int, observed.split()))
    if (
        len(values) != len(frontend.FIELDS) + 1
        or dict(zip([n for n, _ in frontend.FIELDS], values[:-1], strict=True))
        != cold["default_native_state"]
        or values[-1] != cold["default_native_pins"]
    ):
        raise ValueError("Changed freshly observed native source initialization")
    work.joinpath("initial.log").write_text(observed)
    state = 0
    for (_, width), value in zip(frontend.FIELDS, values[:-1], strict=True):
        state = (state << width) | value
    text = PROFILE.replace("INITIAL_STATE", f"{state:064x}").replace(
        "NORMALIZED_STATE", f"{state ^ (1 << 169):064x}"
    )
    artifacts = [work / "initial.log"]
    checks = []
    for name, old, new in (
        ("positive", None, None),
        (
            "lost-lda-zero-update",
            "case (0xA9<<3)|1: c->A=_GD();_NZ(c->A);_FETCH();break;",
            "case (0xA9<<3)|1: c->A=_GD();c->P=(c->P&~M6502_NF)|(c->A&M6502_NF);_FETCH();break;",
        ),
    ):
        case = work / name
        case.mkdir(exist_ok=True)
        source = case / "reference.h"
        model = case / "source.v"
        source.write_text(replace_once(header.read_text(), old, new) if old else header.read_text())
        expanded = subprocess.check_output(
            ["gcc", "-E", "-P", "-"], input=frontend.extract(source), text=True
        )
        model.write_text(
            prune.prune(structural.generate(short_stream.adapter_ast(expanded, [0, 0xA9])))[0]
        )
        artifacts += [source, model]
        if old is None:
            small, prop, graph, log, witness = [
                case / n
                for n in (
                    "reduced.v",
                    "preservation.v",
                    "preservation.json",
                    "preservation.log",
                    "preservation-witness.json",
                )
            ]
            small.write_text(
                replace_once(model.read_text(), "module M6502ReferenceNext(", "module Reduced(")
            )
            prop.write_text(
                replace_once(
                    reset_sequence.PRESERVATION,
                    "wire context=(pins[25] && pins[30] && !pins[28]) || (!pins[25] && state[255:243]==0);",
                    "wire context=pins[25] ? (pins[30] || pins[23:16]==0 || pins[23:16]==8'ha9) : (state[255:251]==0 && (state[250:243]==0 || state[250:243]==8'ha9));",
                )
            )
            prep = case / "preservation"
            prep.mkdir(exist_ok=True)
            command = f"read_verilog -formal -sv {full} {small} {prop}; prep -top ResetDecoderPreservation -flatten; check -assert; opt; write_json {graph}"
            prepared.prepare(prep, command, [full, small, prop], [graph], timeout=1200)
            accepted = reset_sequence.run_query(
                graph, "ResetDecoderPreservation", log, witness, "equivalent"
            )
            checks.append({"name": "decoder-preservation", "accepted": accepted})
            artifacts += [small, prop, graph, log, prep / "prepared.json", prep / "prepare.log"]
            if witness.exists():
                artifacts.append(witness)
            print(
                "Source startup decoder preservation: " + ("PASS" if accepted else "FAIL"),
                flush=True,
            )
            if not accepted:
                break
        prop, graph, log, witness = [
            case / n for n in ("contract.v", "graph.json", "proof.log", "witness.json")
        ]
        prop.write_text(text)
        command = f"read_verilog -formal -sv {model} {prop}; prep -top SourceOsStartupProfile -flatten; check -assert; opt; memory_map; opt; write_json {graph}"
        cached = prepared.prepare(case, command, [model, prop], [graph], timeout=600)
        # Only the negative forces no restart, so the first LDA must retire.
        if old:
            design = json.loads(graph.read_text())
            graph = case / "fixed.json"
            graph.write_text(
                json.dumps(specialized.substitute(design, "SourceOsStartupProfile", {"warm": 0}))
            )
            specialized.check_constants(
                json.loads(graph.read_text()), "SourceOsStartupProfile", {"warm": 0}
            )
        accepted = reset_sequence.run_query(
            graph,
            "SourceOsStartupProfile",
            log,
            witness,
            "faultDefined" if old else "equivalent",
            sequence=14,
            positive=old is None,
        )
        checks.append({"name": name, "accepted": accepted, "preparation_cache_reused": cached})
        artifacts += [prop, case / "graph.json", log, case / "prepared.json", case / "prepare.log"]
        if old:
            artifacts.append(graph)
        if witness.exists():
            artifacts.append(witness)
        print("Source startup profile " + name + (": PASS" if accepted else ": FAIL"), flush=True)
        if not accepted:
            break
    for p in paths:
        memory_composition.sealed(p)
    stable = before == hashes(inputs)
    passed = stable and len(checks) == 3 and all(c["accepted"] for c in checks)
    write_json(
        ROOT / "build/bbc/cpu-c-os-startup-profile/result.json",
        {
            "passed": passed,
            "inputs_unchanged": stable,
            "inputs": before,
            "artifacts": hashes(artifacts),
            "checks": checks,
            "scope": "Fourteen accepted source ticks from freshly observed real m6502_init, comparing a counterpart differing only in initial Z. Verified original reset vector and first LDA #$40 bytes; other read bytes and restart requests are arbitrary. No writes before the boundary. Only initial Z and its carried discarded-read-data bits in pins/saved PINS are masked before retirement; complete state and pins converge when the LDA retires. Decoder preservation precedes the history check. Actual C loss of LDA's Z update must produce an entirely defined failing history with no restart.",
            "limits": "Bounded source startup-profile normalization. Freeze after first LDA retirement; convergence is conditional, not a liveness claim. IRQ/NMI quiet through this prefix. No actual RTL/C correspondence, physical enables or whole-machine/game composition asserted.",
            "universal_game_equivalence": False,
        },
    )
    if not passed:
        raise ValueError("Source OS startup profile incomplete")


if __name__ == "__main__":
    qualify()
