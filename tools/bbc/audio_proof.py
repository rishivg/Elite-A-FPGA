"""Prove the shared component preserves 32 MHz wiring with the corrected reset."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

from tools.validation.common import ROOT, write_json

from . import baselines, build
from .probe import device_rtl

BASELINE = "4abd5fa8576553ccb2cc61a136bbecee090c72d3"
SHARED = ["rtl/bbc/BbcSoundBus.scala"]


def qualify():
    if not build.generation_current():
        raise ValueError("Run make bbc-gen before proving current audio RTL")
    for path in SHARED:
        original = baselines.historical(BASELINE, path)
        current = (ROOT / path).read_text()
        if path.endswith("BbcSoundBus.scala"):
            # Only the explicit reset option differs from the historical bus.
            # Its default body must still match byte-for-token; the actual
            # machine and both proof sides select the corrected pending window.
            current = current.replace(
                "case class BbcSoundBus(resetPending: Boolean = false)",
                "case class BbcSoundBus()",
            ).replace("init (if (resetPending) 1 else 225)", "init (225)")

            def tokens(source):
                source = re.sub(r"/\*[\s\S]*?\*/|//[^\n]*", "", source)
                return re.sub(r"\s+", "", source)

            current, original = tokens(current), tokens(original)
        if current != original:
            raise ValueError("Shared audio baseline changed: " + path)
    directory = build.BBC / "audio-proof"
    directory.mkdir(parents=True, exist_ok=True)
    dependencies = [
        *baselines.dependencies(BASELINE),
        Path(__file__),
        ROOT / "tools/bbc/probe.py",
        ROOT / "sim/formal/BbcAudioBaseline.scala",
        ROOT / "rtl/bbc/BbcAudio.scala",
        ROOT / "rtl/bbc/BbcMachine.scala",
        *[ROOT / path for path in SHARED],
        ROOT / "rtl/audio/Psg.scala",
        ROOT / "build.sbt",
        ROOT / "project/build.properties",
    ]
    before = build.hashes(dependencies)
    production = device_rtl(["BbcAudio", "BbcSoundBus", "Psg"])
    machine = (build.BBC / "BbcMachine.v").read_text()
    # Specialize to the actual constant enable of BbcMachine(1), rather than
    # assuming that arbitrary 96 MHz enable sequences preserve 32 MHz timing.
    top = re.search(r"^module BbcMachine\b[\s\S]*?^endmodule\b", machine, re.M).group()
    if not (
        re.search(r"BbcAudio\s+audio\s*\([\s\S]*?\.io_clock32\s*\(clock32\s*\)", top)
        and len(re.findall(r"\bassign\s+clock32\s*=\s*1'b1\s*;", top)) == 1
    ):
        raise ValueError("The 32 MHz machine does not supply the expected constant audio enable")
    production, count = re.subn(r"^\s*input\s+wire\s+io_clock32,\n", "", production, flags=re.M)
    if count != 1:
        raise ValueError("Unrecognized audio enable port")
    endpoint = production.index(");", production.index("module BbcAudio ")) + 2
    production = production[:endpoint] + "\n  wire io_clock32 = 1'b1;" + production[endpoint:]
    (directory / "gate.v").write_text(production)
    with (directory / "generate.log").open("w") as log:
        subprocess.run(
            [
                os.environ.get("SBT", "sbt"),
                'set Compile / unmanagedSourceDirectories += file("sim/formal")',
                "runMain leet.bbc.GenerateBbcAudioBaseline",
            ],
            cwd=ROOT,
            stdout=log,
            stderr=subprocess.STDOUT,
            check=True,
            timeout=300,
        )
    golden = directory / "golden/BbcAudioBaseline.v"
    for label, top, path in [
        ("gold", "BbcAudioBaseline", golden),
        ("gate", "BbcAudio", directory / "gate.v"),
    ]:
        command = (
            f"read_verilog -sv {path}; hierarchy -top {top}; proc; memory; opt; "
            f"flatten; opt; rename {top} Audio{label}; write_rtlil {label}.il"
        )
        with (directory / (label + "-prepare.log")).open("w") as log:
            subprocess.run(
                ["yosys", "-Q", "-T", "-p", command],
                cwd=directory,
                stdout=log,
                stderr=subprocess.STDOUT,
                check=True,
                timeout=120,
            )
    artifacts = [golden, directory / "gate.v", directory / "gold.il", directory / "gate.il"]
    generated = build.hashes(artifacts)
    command = (
        "read_rtlil gold.il gate.il; equiv_make Audiogold Audiogate audio_equiv; "
        "hierarchy -top audio_equiv; async2sync; equiv_simple; equiv_induct -seq 2; "
        "equiv_status -assert"
    )
    with (directory / "proof.log").open("w") as log:
        subprocess.run(
            ["yosys", "-Q", "-T", "-p", command],
            cwd=directory,
            stdout=log,
            stderr=subprocess.STDOUT,
            check=True,
            timeout=120,
        )
    if (
        not build.generation_current()
        or before != build.hashes(dependencies)
        or generated != build.hashes(artifacts)
        or machine != (build.BBC / "BbcMachine.v").read_text()
    ):
        raise ValueError("Audio preservation proof inputs changed")
    output = (directory / "proof.log").read_text()
    count = re.search(r"Of those cells (\d+) are proven and 0 are unproven", output)
    if count is None or "Equivalence successfully proven!" not in output:
        raise ValueError("Incomplete audio preservation proof")
    result = {
        "passed": True,
        "proven_cells": int(count[1]),
        "unproven_cells": 0,
        "baseline_commit": BASELINE,
        "baseline_reset": "Both wiring variants use the corrected active Model B reset window. This does not preserve the historical first-write timestamp.",
        "baseline_psg": "Both wiring variants use the current PSG, including the BBC linked-zero noise correction. This proves the component's clock wiring, not preservation of the historical PSG behavior.",
        "inputs": before,
        "generated": generated,
        "machine": build.hashes([build.BBC / "BbcMachine.v"]),
        "yosys": subprocess.check_output(["yosys", "-V"], text=True).strip(),
        "command": command,
        "scope": "Every audio output with the actual 32 MHz machine's constant clock enable, reset and unrestricted enabled/touch/data inputs. No output exclusions or input assumptions.",
        "limits": "Preservation of the earlier clock wiring at 32 MHz with corrected child reset and PSG semantics on both sides. Independent sound correctness, 96 MHz acceptance/sampling tests and whole-game replay remain separate obligations.",
    }
    write_json(directory / "result.json", result)
    print(f"Audio32 preservation: {count[1]} proven cells; 0 unproven")
    return result


if __name__ == "__main__":
    qualify()
