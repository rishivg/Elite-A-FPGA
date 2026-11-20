"""Compare real RTL reset/startup requests with the executing C reference."""

import json
import shutil
import subprocess
import time
from pathlib import Path

from tools.bbc import prepare
from tools.bbc.proofs import memory_composition
from tools.bbc.proofs.cpu_reference import (
    foundation,
    frontend,
    prepared,
    prune,
    short_stream,
    structural,
)
from tools.bbc.proofs.cpu_reference.warm_checks import accepted_result
from tools.bbc.proofs.video_cursor import hashes, replace_once
from tools.validation.common import LOCK, ROOT, digest, source_root, write_json

DIRECTORY = ROOT / "build/bbc/cpu-source-reset-rtl"


def native_initialization(binary, report):
    values = list(map(int, subprocess.check_output([str(binary)], text=True).split()))
    names = [name for name, _ in frontend.FIELDS]
    if len(values) != len(names) + 1:
        raise ValueError("Incomplete native initialization")
    initial = dict(zip(names, values[:-1], strict=True))
    if initial != report["default_native_state"] or values[-1] != report["default_native_pins"]:
        raise ValueError("Changed native initialization")
    packed = 0
    for name, width in frontend.FIELDS:
        packed = (packed << width) | initial[name]
    return packed, values[-1]


def preservation():
    return """module StartupDecoderPreservation(output equivalent);
(* anyseq *) reg [255:0] state;
(* anyseq *) reg [63:0] pins;
wire [255:0] fullState,smallState;wire [63:0] fullPins,smallPins;
wire fullTrap,smallTrap;
Full full(.state_in(state),.pins_in(pins),.state_out(fullState),.pins_out(fullPins),.trap(fullTrap));
M6502ReferenceNext small(.state_in(state),.pins_in(pins),.state_out(smallState),.pins_out(smallPins),.trap(smallTrap));
function supported;input [7:0] op;begin supported=op==0 || op==8'ha9 || op==8'h8d || op==8'hea;end endfunction
wire context=(pins[24] && pins[28]) ||
 (pins[25] ? (pins[30] || supported(pins[23:16])) : (state[255:251]==0 && supported(state[250:243])));
assign equivalent=!context || (fullState==smallState && fullPins==smallPins && fullTrap==smallTrap);
endmodule
"""


def qualify():
    started = time.monotonic()
    DIRECTORY.mkdir(exist_ok=True)
    paths = [foundation.DIRECTORY / "result.json"]
    full_report = translation = cold = memory_composition.sealed(paths[0])
    full = foundation.DIRECTORY / "source.v"
    native = foundation.DIRECTORY / "initial"
    header = ROOT / "build/bbc/reference-cycle/m6502.h"
    for path, report, group in (
        (full, full_report, "artifacts"),
        (native, cold, "artifacts"),
        (header, translation, "inputs"),
    ):
        key = str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path)
        if report[group].get(key) != digest(path):
            raise ValueError("Unbound source model or initialization: " + str(path))
    cpu = ROOT / "build/bbc/ArletCpuEnabled.v"
    original_core = ROOT / "third_party/6502/cpu.v"
    alu = ROOT / "third_party/6502/ALU.v"
    derived = (
        prepare.enabled_cpu(original_core.read_text()) + "\n" + prepare.nmos_alu(alu.read_text())
    )
    if cpu.read_text() != derived:
        raise ValueError("Generated CPU differs from the maintained derivation")
    os_rom = source_root("jsbeeb") / "public/roms/os.rom"
    if digest(os_rom) != LOCK["assets"]["jsbeeb:public/roms/os.rom"]:
        raise ValueError("Changed OS ROM")
    image = os_rom.read_bytes()
    vector = image[0x3FFC] | image[0x3FFD] << 8
    if image[vector - 0xC000 : vector - 0xC000 + 3] != bytes([0xA9, 0x40, 0x8D]):
        raise ValueError("Changed OS startup instruction bytes")
    inputs = [
        Path(__file__),
        Path(__file__).with_suffix(".v"),
        Path(prepare.__file__),
        *[
            Path(m.__file__)
            for m in (
                foundation,
                frontend,
                prepared,
                prune,
                short_stream,
                structural,
                memory_composition,
            )
        ],
        Path(__file__).with_name("warm_checks.py"),
        Path(__file__).parents[1] / "video_cursor.py",
        *paths,
        full,
        native,
        header,
        cpu,
        original_core,
        alu,
        ROOT / "rtl/bbc/BbcCpu.v",
        ROOT / "tools/validation/reference.lock.json",
        os_rom,
        Path(shutil.which("yosys")),
        Path(shutil.which("gcc")),
    ]
    before = hashes(inputs)
    initial, pins = native_initialization(native, cold)
    expanded = subprocess.check_output(
        ["gcc", "-E", "-P", "-"], input=frontend.extract(header), text=True
    )
    model = DIRECTORY / "source.v"
    model.write_text(
        prune.prune(structural.generate(short_stream.adapter_ast(expanded, [0, 0xA9, 0x8D])))[0]
    )
    complete = DIRECTORY / "full.v"
    complete.write_text(
        replace_once(full.read_text(), "module M6502ReferenceNext(", "module Full(")
    )
    property_file = DIRECTORY / "preservation.v"
    property_file.write_text(preservation())
    graph = DIRECTORY / "preservation.json"
    cached = prepared.prepare(
        DIRECTORY,
        f"read_verilog -formal -sv {complete} {model} {property_file}; prep -top StartupDecoderPreservation -flatten; check -assert; opt; write_json {graph}",
        [complete, model, property_file],
        [graph],
    )
    log = DIRECTORY / "preservation.log"
    with log.open("w") as output:
        run = subprocess.run(
            [
                "yosys",
                "-Q",
                "-T",
                "-p",
                f"read_json {graph}; sat -prove equivalent 1 -verify -timeout 120",
            ],
            stdout=output,
            stderr=subprocess.STDOUT,
            timeout=180,
        )
    if run.returncode != 0 or "no model found: SUCCESS!" not in log.read_text():
        raise ValueError("Startup decoder preservation failed")
    checks = [
        {
            "name": "complete-decoder-preservation",
            "accepted": True,
            "preparation_cache_reused": cached,
        }
    ]
    contract = (
        (Path(__file__).with_suffix(".v"))
        .read_text()
        .replace("INITIAL_STATE", f"{initial:064x}")
        .replace("INITIAL_PINS", f"{pins:016x}")
    )
    wrapper = ROOT / "rtl/bbc/BbcCpu.v"
    variants = [
        ("positive", contract),
        (
            "same-edge-response",
            replace_once(contract, ".read_data(heldResponse)", ".read_data(requestedData)"),
        ),
        ("lost-enable", replace_once(contract, ".enable(accepted)", ".enable(1'b1)")),
        (
            "lost-vector-bit",
            replace_once(
                contract, ".read_data(heldResponse)", ".read_data({heldResponse[7:1],1'b0})"
            ),
        ),
    ]
    for name, body in variants:
        case = DIRECTORY / name
        case.mkdir(exist_ok=True)
        source, graph, log, witness = [
            case / filename
            for filename in ("contract.v", "graph.json", "proof.log", "witness.json")
        ]
        source.write_text(body)
        prepared.prepare(
            case,
            f"read_verilog -formal -sv {model} {wrapper} {cpu} {alu} {source}; prep -top SourceRtlReset -flatten; check -assert; async2sync; opt; memory_map; opt; techmap; opt; abc -g AND; opt_clean; write_json {graph}",
            [model, wrapper, cpu, alu, source],
            [graph],
        )
        if any(
            cell["type"] in ("$assume", "$check")
            for cell in json.loads(graph.read_text())["modules"]["SourceRtlReset"]["cells"].values()
        ):
            raise ValueError("Reset/RTL comparison acquired an assumption")
        positive = name == "positive"
        mode = "-tempinduct -initsteps 12 -maxsteps 18" if positive else "-seq 16"
        witness.unlink(missing_ok=True)
        prop = "equivalent" if positive else "faultDefined"
        with log.open("w") as output:
            run = subprocess.run(
                [
                    "yosys",
                    "-Q",
                    "-T",
                    "-p",
                    f"read_json {graph}; sat -set-init-zero {mode} -prove {prop} 1 -verify -timeout 300 -show phase -show {prop} -dump_json {witness}",
                ],
                stdout=output,
                stderr=subprocess.STDOUT,
                timeout=900,
            )
        accepted = (
            (run.returncode == 0 and "Induction step proven: SUCCESS!" in log.read_text())
            if positive
            else accepted_result(run.returncode, log.read_text(), witness, prop, False)
        )
        checks.append({"name": name, "accepted": accepted})
        print(name + (": PASS" if accepted else ": FAIL"), flush=True)
        if not accepted:
            break
    for path in paths:
        memory_composition.sealed(path)
    stable = before == hashes(inputs)
    passed = stable and len(checks) == 5 and all(c["accepted"] for c in checks)
    write_json(
        DIRECTORY / "result.json",
        {
            "passed": passed,
            "wall_seconds": time.monotonic() - started,
            "inputs_unchanged": stable,
            "inputs": before,
            "artifacts": hashes(
                [p for p in DIRECTORY.rglob("*") if p.is_file() and p != DIRECTORY / "result.json"]
            ),
            "checks": checks,
            "os_vector": vector,
            "scope": "Actual RTL CPU versus preservation-checked executing C reset/startup decoder. Native C initialization, arbitrary discarded/vector/read bytes and arbitrary accepted/disabled edges. Every address, RW and SYNC request is compared; all meaningful flags and general registers are compared after the first LDA #$40 retires at the following STA decode. No source or DUT state injection and no SAT input assumptions. Conditional completion permits indefinite waits.",
            "limits": "Initial logical zero/reset profile; quiet IRQ/NMI and no warm requests. Verified first OS instruction bytes follow an arbitrary reset vector. Execution freezes after the first retirement comparison. Memory-response completion, actual parent clocks, subsequent mixed instruction/interrupt/reset histories and universal game equivalence remain separate.",
            "universal_game_equivalence": False,
        },
    )
    if not passed:
        raise ValueError("Source/RTL startup proof incomplete")


if __name__ == "__main__":
    qualify()
