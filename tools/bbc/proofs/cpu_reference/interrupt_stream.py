"""Investigate source C short/branch/BRK streams with unrestricted IRQ/NMI."""

import argparse
import json
import re
import shutil
import subprocess
from pathlib import Path

from tools.bbc.proofs import branch_induction, memory_composition
from tools.bbc.proofs.cpu_reference import eligible_stream, instructions, prepared, quiet_stream
from tools.bbc.proofs.video_cursor import hashes, replace_once
from tools.validation.common import ROOT, write_json


def prefix(initial, pins, original):
    text = eligible_stream.prefix(initial, pins, original)
    text = replace_once(
        text,
        "module BbcCpuStreamPrefix(",
        "module BbcCpuInterruptStreamPrefix #(parameter ALLOW_NMI=1)(",
    )
    text = replace_once(text, "input clk,", "input clk, input irqPin, input nmiPin,")
    text = replace_once(text, "!trap && entryInvariant &&", "!trap &&")
    # The specification samples its previous NMI level on the reset edge,
    # even when this abstract wrapper already starts at age 26. Use that same
    # entry history; other prior temporary state stays symbolic. This never
    # assigns executing FPGA state or constrains later NMI transitions.
    text = replace_once(
        text,
        ",hiddenPins,",
        ",((hiddenPins & ~64'h8000000) | ({63'b0,ALLOW_NMI && nmiPin}<<27)),",
    )
    text = replace_once(
        text,
        "wire [63:0] response=(referencePins & ~64'hff0000) | ({56'b0,returnedData}<<16);",
        "wire [63:0] response=(referencePins & ~64'hcff0000) | ({56'b0,returnedData}<<16) | "
        "({63'b0,age>=26 && irqPin}<<26) | ({63'b0,age>=26 && ALLOW_NMI && nmiPin}<<27);",
    )
    fields = instructions.fields("referenceState")
    # A forced interrupt discards the opcode without advancing the C engine's
    # execution PC. The RTL retirement diagnostic still describes the discarded
    # fetch plus one. Observe that phase without changing C state or bus pins.
    return replace_once(
        text,
        f"assign pc=previousFetch ? referencePins[15:0] : {fields['PC']};",
        f"assign pc=previousFetch ? referencePins[15:0]+({fields['brk_flags']}!=0 ? 16'd1 : 16'd0) : {fields['PC']};",
    )


def qualify(depth=24, protocol=False):
    if depth < 12:
        raise ValueError("Interrupt investigation must span at least twelve transitions")
    cold = ROOT / "build/bbc/cpu-c-cold-entry-bridge/result.json"
    initialized = memory_composition.sealed(cold)
    stream_premise = ROOT / "build/bbc/cpu-c-quiet-stream-bridge/result.json"
    stream = memory_composition.sealed(stream_premise)
    model = Path("/tmp/fpgaleet-c-quiet-stream/positive/source.v")
    if stream["artifacts"].get(str(model)) != hashes([model])[str(model)]:
        raise ValueError("Changed complete short/branch/BRK decoder")
    ordinary = ROOT / "sim/formal/BbcCpuStreamPrefix.v"
    original_contract = ROOT / "sim/formal/BbcCpuInterruptStreamContract.v"
    architecture = ROOT / "sim/formal/BbcCpuArchitectureContract.v"
    suffix = "-protocol" if protocol else ""
    work = Path("/tmp/fpgaleet-c-interrupt-stream" + suffix)
    work.mkdir(exist_ok=True)
    directory = ROOT / ("build/bbc/cpu-c-interrupt-stream" + suffix + "-investigation")
    directory.mkdir(exist_ok=True)
    contract = original_contract
    if protocol:
        contract = work / "contract.v"
        text = original_contract.read_text()
        domains = re.findall(r"^always @\* assume\([^\n]+\);$", text, re.M)
        if len(domains) != 1:
            raise ValueError("Changed single interrupt opcode domain")
        contract.write_text(
            replace_once(
                text,
                domains[0],
                "always @* assume((firstOpcode==0 || firstOpcode==8'hea) && (nextOpcode==0 || nextOpcode==8'hea));",
            )
        )
    files = [
        Path(__file__),
        Path(eligible_stream.__file__),
        Path(instructions.__file__),
        Path(quiet_stream.__file__),
        Path(prepared.__file__),
        Path(branch_induction.__file__),
        Path(memory_composition.__file__),
        Path(__file__).parents[1] / "video_cursor.py",
        cold,
        stream_premise,
        model,
        ordinary,
        original_contract,
        contract,
        architecture,
        Path(shutil.which("yosys")),
    ]
    before = hashes(files)
    wrapper = work / "prefix.v"
    wrapper.write_text(
        prefix(
            initialized["default_native_state"],
            initialized["default_native_pins"],
            ordinary.read_text(),
        )
    )
    log, graph, mapped, witness = [
        work / n for n in ("proof.log", "graph.json", "mapped.json", "witness.json")
    ]
    witness.unlink(missing_ok=True)
    top = "BbcCpuInterruptStreamContract"
    preparation = (
        f"read_verilog -formal -sv {model}; read_verilog -formal {wrapper} {contract} {architecture}; "
        f"prep -top {top} -flatten; check -assert; opt; chformal -lower; write_json {graph}; "
        f"techmap; opt; abc -g AND; opt_clean; write_json {mapped}"
    )
    cached = prepared.prepare(
        work, preparation, [model, wrapper, contract, architecture], [graph, mapped]
    )
    command = (
        f"read_json {mapped}; sat -set-assumes -set-init-zero -seq {depth} "
        f"-prove equivalent 1 -verify -timeout 180 -show equivalent -show step -show stage "
        f"-show firstOpcode -show nextOpcode -show irqPin -show nmiPin "
        "-show prefix.referenceState -show prefix.referencePins -show prefix.retirementState "
        "-show flags -show instruction "
        "-show currentPc -show committed -show modelP -show visibleP -show nmiPending "
        "-show nmiPolled -show irqPolled -show address -show expectedAddress "
        f"-show hardwareInterrupt -dump_json {witness}"
    )
    with log.open("w") as output:
        run = subprocess.run(
            ["yosys", "-Q", "-T", "-p", command],
            stdout=output,
            stderr=subprocess.STDOUT,
            timeout=600,
        )
    branch_induction.guard_domains(json.loads(graph.read_text()), top)
    branch_induction.guard_domains(json.loads(mapped.read_text()), top)
    stable = before == hashes(files)
    passed = stable and run.returncode == 0 and "no model found: SUCCESS!" in log.read_text()
    write_json(
        directory / "result.json",
        {
            "passed": passed,
            "inputs_unchanged": stable,
            "inputs": before,
            "artifacts": hashes([p for p in work.iterdir() if p.is_file()]),
            "cycles": depth,
            "protocol_only": protocol,
            "preparation_cache_reused": cached,
            "scope": (
                "Bounded investigative NOP/BRK interrupt protocol stream; the only property change restricts its two symbolic opcode slots to NOP/BRK. "
                if protocol
                else "Bounded investigative source C short/branch/BRK stream. "
            )
            + "Quiet architectural fetch entry, arbitrary subsequent IRQ/NMI and response bytes. Unchanged interrupt stream bus/architecture checks and source execution state; no opcode cases removed from the sealed short/branch/BRK source model.",
            "limits": "Investigation only: no induction, opcode admission, actual source fault controls or full decoder partition yet. The abstract reference entry's previous NMI level equals the specification's reset sample; source cold-prefix composition remains separate. Compiler trust, interrupt history composition, arbitrary enables, warm reset and whole-game equivalence remain open.",
            "universal_game_equivalence": False,
        },
    )
    print("C interrupt stream investigation: " + ("PASS" if passed else "FAIL"), flush=True)
    if not passed:
        raise ValueError("C interrupt stream investigation incomplete; see retained logs/witness")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cycles", type=int, default=24)
    parser.add_argument("--protocol-only", action="store_true")
    args = parser.parse_args()
    qualify(args.cycles, args.protocol_only)
