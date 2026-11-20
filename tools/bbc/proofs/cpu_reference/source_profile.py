"""Prove the Model B source profile survives every defined CPU transition."""

import json
import shutil
import subprocess
from pathlib import Path

from pycparser import c_parser

from tools.bbc.proofs import memory_composition
from tools.bbc.proofs.cpu_reference import foundation, frontend, prepared, prune, structural
from tools.bbc.proofs.cpu_reference.warm_checks import accepted_result
from tools.bbc.proofs.video_cursor import hashes
from tools.validation.common import ROOT, write_json

DIRECTORY = ROOT / "build/bbc/cpu-source-profile"
USED_PINS = 0x5FFFFFFF

CONTRACT = """module SourceProfile(output equivalent);
(* anyseq *) reg [255:0] state;
(* anyseq *) reg [63:0] pins;
wire [255:0] following;
wire [63:0] nextPins;
wire trap;
M6502ReferenceNext source(.state_in(state),.pins_in(pins),.state_out(following),.pins_out(nextPins),.trap(trap));
function profile;
 input [255:0] s;
 input [63:0] p;
 begin profile=s[39:32]==1 && s[31:0]==0 && !s[173] && s[96:88]==0 &&
 ((s[167:104]&~64'h5fffffff)==0) && ((p&~64'h5fffffff)==0);end
endfunction
assign equivalent=!profile(state,pins) || trap || profile(following,nextPins);
endmodule
"""


def qualify():
    parent_path = foundation.DIRECTORY / "result.json"
    parent = memory_composition.sealed(parent_path)
    DIRECTORY.mkdir(parents=True, exist_ok=True)
    header = ROOT / "build/bbc/reference-cycle/m6502.h"
    full = foundation.DIRECTORY / "source.v"
    files = [
        Path(__file__),
        parent_path,
        header,
        full,
        *[
            Path(module.__file__)
            for module in (foundation, frontend, prepared, prune, structural, memory_composition)
        ],
        Path(__file__).with_name("warm_checks.py"),
        Path(__file__).parents[1] / "video_cursor.py",
        ROOT / "tools/validation/common.py",
        *[Path(shutil.which(tool)) for tool in ("gcc", "yosys")],
    ]
    before = hashes(files)
    native = parent["default_native_state"]
    initial_profile = (
        native["bcd_enabled"] == 1
        and native["P"] & 32 == 0
        and native["irq_pip"] & 511 == 0
        and all(native[name] == 0 for name in ("io_ddr", "io_out", "io_inp", "io_pins"))
        and native["PINS"] & ~USED_PINS == 0
        and parent["default_native_pins"] & ~USED_PINS == 0
    )
    if not initial_profile:
        raise ValueError("Native constructor no longer supplies the Model B profile")
    property_file = DIRECTORY / "contract.v"
    property_file.write_text(CONTRACT)
    cases = [("positive", full, True)]
    for name, statement in (
        ("lost-decimal-capability", "c->bcd_enabled=false;"),
        ("nonzero-6510-port", "c->io_pins=1;"),
    ):
        case = DIRECTORY / name
        case.mkdir(exist_ok=True)
        altered = case / "reference.h"
        # The last occurrence belongs to the executing tick's return path;
        # keep initialization, helpers and every dispatch case unchanged.
        marker = "    M6510_SET_PORT(pins, c->io_pins);"
        original = header.read_text()
        if original.count(marker) != 2:
            raise ValueError("Changed maintained source tick return paths")
        offset = original.rindex(marker)
        altered.write_text(original[:offset] + statement + "\n" + original[offset:])
        expanded = subprocess.check_output(
            ["gcc", "-E", "-P", "-"], input=frontend.extract(altered), text=True
        )
        model = case / "source.v"
        model.write_text(prune.prune(structural.generate(c_parser.CParser().parse(expanded)))[0])
        cases.append((name, model, False))
    checks = []
    for name, model, positive in cases:
        case = DIRECTORY / name
        case.mkdir(exist_ok=True)
        graph, log, witness = [
            case / filename for filename in ("graph.json", "proof.log", "witness.json")
        ]
        prepared.prepare(
            case,
            f"read_verilog -formal -sv {model} {property_file}; prep -top SourceProfile -flatten; check -assert; opt; memory_map; opt; techmap; opt; abc -g AND; opt_clean; write_json {graph}",
            [model, property_file],
            [graph],
            timeout=1800,
        )
        module = json.loads(graph.read_text())["modules"]["SourceProfile"]
        if any(cell["type"] in ("$assume", "$check") for cell in module["cells"].values()):
            raise ValueError("Source profile acquired an input assumption")
        witness.unlink(missing_ok=True)
        with log.open("w") as output:
            trial = subprocess.run(
                [
                    "yosys",
                    "-Q",
                    "-T",
                    "-p",
                    f"read_json {graph}; sat -prove equivalent 1 -verify -timeout 120 -show equivalent -dump_json {witness}",
                ],
                stdout=output,
                stderr=subprocess.STDOUT,
                timeout=300,
            )
        accepted = accepted_result(
            trial.returncode, log.read_text(), witness, "equivalent", positive
        )
        checks.append({"name": name, "accepted": accepted})
        print("Source profile " + name + (": PASS" if accepted else ": FAIL"), flush=True)
        if not accepted:
            break
    memory_composition.sealed(parent_path)
    stable = before == hashes(files)
    passed = stable and len(checks) == 3 and all(check["accepted"] for check in checks)
    artifacts = [
        property_file,
        *[path for name, _, _ in cases for path in (DIRECTORY / name).glob("*") if path.is_file()],
    ]
    write_json(
        DIRECTORY / "result.json",
        {
            "passed": passed,
            "inputs_unchanged": stable,
            "inputs": before,
            "artifacts": hashes(artifacts),
            "native_constructor_profile": initial_profile,
            "checks": checks,
            "scope": "Native C construction establishes decimal capability, cleared unused status bit, zero low IRQ pipeline bits, zero 6510 state and unused live/saved pins. A fresh combinational theorem preserves this profile through every defined complete-source transition, including fetch, executing and read-hold domains. Incoming data/IRQ/NMI/reset controls remain arbitrary inside used pins. Actual C mutations losing decimal capability or setting a 6510 port must fail on defined profile transitions. Constructor plus transition theorem supports ordinary induction over any legal reference execution.",
            "limits": "A source-state invariant supporting reset/history interface premises. Does not prove RTL interrupt mapping, instruction eligibility, memory/scheduler composition or whole-game equivalence.",
            "universal_game_equivalence": False,
        },
    )
    if not passed:
        raise ValueError("Source profile proof incomplete")


if __name__ == "__main__":
    qualify()
