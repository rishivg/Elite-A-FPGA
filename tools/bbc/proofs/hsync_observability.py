"""Check where the raw CRTC horizontal-sync signal can affect the actual machine."""

import argparse
import json
import shutil
import subprocess
from pathlib import Path

from tools.bbc.proofs import current_machine, memory_composition
from tools.bbc.proofs.video_cursor import hashes
from tools.validation.common import ROOT, write_json

DIRECTORY = ROOT / "build/bbc/hsync-observability"


def hsync_storage(graph, top):
    module = graph["modules"][top]
    return [name for name in module["netnames"] if name.endswith((".hsync", ".hcount"))]


def boot_register_history(lines):
    """Read accepted CRTC register writes from a complete cold machine trace."""
    index = r0 = r2 = width = 0
    first = None
    changes = 0
    for line in lines:
        cycle, address, value, *_ = map(int, line.split())
        if not 0xFE00 <= address < 0xFE08:
            continue
        if not address & 1:
            index = value & 31
        elif index == 0:
            r0 = value
        elif index == 2:
            r2 = value
        elif index == 3:
            width = value & 15
            changes += 1
            if width and first is None:
                if r0 or r2:
                    raise ValueError("Initial width was configured after R0 or R2")
                first = {"master_cycle_32mhz_units": cycle, "register_r3": value}
        if not width and (r0 or r2 or first is not None):
            raise ValueError("Cold capture left the blank width-zero startup domain")
    if first is None:
        raise ValueError("Cold trace never configures horizontal sync")
    return {"first_nonzero_width": first, "observed_r3_writes": changes}


def qualify(boot_receipt=None):
    current_machine.audit()
    DIRECTORY.mkdir(parents=True, exist_ok=True)
    parents = current_machine.parents()
    cpu = [ROOT / "rtl/bbc/BbcCpu.v", ROOT / "build/bbc/ArletCpuEnabled.v"]
    files = [
        Path(__file__),
        Path(current_machine.__file__),
        *cpu,
        *[path for _, path, _ in parents],
        Path(shutil.which("yosys")),
        ROOT / "build/bbc/equivalence-current/generation.json",
    ]
    before = hashes(files)
    artifacts = []
    for multiplier, path, top in parents:
        baseline, cut, log = (
            DIRECTORY / f"{stem}-{multiplier}.{extension}"
            for stem, extension in (
                ("baseline", "json"),
                ("without-hsync", "json"),
                ("cone", "log"),
            )
        )
        command = (
            f"read_verilog -D SYNTHESIS {path} {cpu[0]} {cpu[1]}; "
            f"hierarchy -check -top {top}; proc; flatten; opt; check -assert; write_json {baseline}; "
            f"delete -output {top}/o:io_hsync; opt -full; opt_clean -purge; check -assert; write_json {cut}"
        )
        with log.open("w") as output:
            subprocess.run(
                ["yosys", "-Q", "-T", "-p", command],
                cwd=path.parent,
                stdout=output,
                stderr=subprocess.STDOUT,
                check=True,
                timeout=120,
            )
        raw, observed = (json.loads(file.read_text()) for file in (baseline, cut))
        original_ports = set(raw["modules"][top]["ports"])
        final_ports = set(observed["modules"][top]["ports"])
        if original_ports - final_ports != {"io_hsync"} or final_ports - original_ports:
            raise ValueError("Changed machine observations while isolating raw HSYNC")
        if len(hsync_storage(raw, top)) != 2 or hsync_storage(observed, top):
            raise ValueError("Horizontal-sync counter or latch reaches another machine observation")
        artifacts.extend((baseline, cut, log))
        print(f"Raw HSYNC is isolated at {multiplier * 32} MHz", flush=True)
    boot_profile, boot_inputs = None, {}
    if boot_receipt is not None:
        receipt = memory_composition.sealed(boot_receipt)
        output_receipt = Path(receipt["run_directory"]) / "whole-machine-outputs.json"
        output = memory_composition.sealed(output_receipt)
        trace = Path(receipt["dut_directory"]) / "bus.tsv"
        if (
            not output["checks"]["all_video_bus_writes"]
            or str(trace.relative_to(ROOT)) not in output["artifacts"]
        ):
            raise ValueError("Missing sealed cold video-bus comparison")
        boot_inputs = hashes([boot_receipt, output_receipt, trace])
        with trace.open() as stream:
            boot_profile = boot_register_history(stream)
        if boot_inputs != hashes([boot_receipt, output_receipt, trace]):
            raise ValueError("Cold video trace changed during width verification")
    if before != hashes(files):
        raise ValueError("Raw HSYNC proof inputs changed")
    write_json(
        DIRECTORY / "result.json",
        {
            "passed": True,
            "inputs_unchanged": True,
            "inputs": before | boot_inputs,
            "artifacts": hashes(artifacts),
            "cold_trace": boot_profile,
            "scope": "In both complete machine elaborations, removing only the raw HSYNC output makes the CRTC horizontal-sync latch and counter dead. Their values cannot affect machine state observable through its other ports, including RGB, VSYNC, bus requests, audio and media.",
            "limits": "The raw pin and downstream monitor behavior retain a different scope. This does not equate width-zero sync between chip models or prove whole-machine video equivalence.",
        },
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--boot-receipt", type=Path)
    qualify(parser.parse_args().boot_receipt)
