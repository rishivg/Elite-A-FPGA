"""Elaborate and check current machines without replacing active simulators."""

import argparse
import json
import os
import shutil
import subprocess
from pathlib import Path

from tools.bbc import (
    build,
    bus_binding_proof,
    cpu_events,
    machine_clock_proof,
    memory_binding_proof,
    rom_proof,
)
from tools.bbc.proofs import (
    adc_scheduler,
    cpu_request_deadline,
    memory_composition,
    memory_deadline,
    startup_interrupts,
)
from tools.bbc.proofs.video_cursor import hashes
from tools.validation.common import ROOT, write_json

DIRECTORY = ROOT / "build/bbc/equivalence-current"
GENERATOR = """package leet.bbc
import spinal.core._
object GenerateCurrentEquivalence extends App {
 for(multiplier <- Seq(1,3)) {
  SpinalConfig(targetDirectory=s"build/bbc/equivalence-current/generated$multiplier",
   defaultConfigForClockDomains=ClockDomainConfig(resetActiveLevel=HIGH))
   .generateVerilog(BbcMachine(clockMultiplier=multiplier).setDefinitionName(if(multiplier==1) "BbcMachine" else "BbcMachine96"))
 }
}
"""


def parents():
    return [
        (m, DIRECTORY / f"generated{m}" / (top + ".v"), top)
        for m, top in ((1, "BbcMachine"), (3, "BbcMachine96"))
    ]


def generation_inputs():
    return {
        **build.generation_inputs(),
        **hashes(
            [
                Path(__file__),
                DIRECTORY / "sources/GenerateCurrentEquivalence.scala",
                Path(shutil.which("sbt")),
            ]
        ),
    }


def generation_outputs():
    return hashes(
        [
            p
            for m, _, _ in parents()
            for p in (DIRECTORY / f"generated{m}").glob("*")
            if p.suffix in (".v", ".bin")
        ]
    )


def audit():
    cpu_events.audit()
    report = json.loads((DIRECTORY / "generation.json").read_text())
    if (
        report.get("passed") is not True
        or not report.get("artifacts")
        or report.get("inputs") != generation_inputs()
        or report.get("artifacts") != generation_outputs()
    ):
        raise ValueError("Current private elaboration is stale")
    return report


def generate():
    cpu_events.prepare()
    sources = DIRECTORY / "sources"
    sources.mkdir(parents=True, exist_ok=True)
    generator = sources / "GenerateCurrentEquivalence.scala"
    if not generator.exists() or generator.read_text() != GENERATOR:
        generator.write_text(GENERATOR)
    before = generation_inputs()
    saved = (
        json.loads((DIRECTORY / "generation.json").read_text())
        if (DIRECTORY / "generation.json").exists()
        else {}
    )
    if saved.get("inputs") == before and saved.get("artifacts") == generation_outputs():
        return audit()
    frozen = [
        build.BBC / "BbcMachine.v",
        build.BBC / "native/VBbcMachine",
        build.BBC / "full-video/BbcMachine96.v",
        build.BBC / "full-video/native/VBbcMachine96",
        build.BBC / "disc.ssd",
        build.BBC / "rom.hex",
    ]
    baseline = hashes(frozen)
    with (DIRECTORY / "generate.log").open("w") as out:
        subprocess.run(
            [
                os.environ.get("SBT", "sbt"),
                'set Compile / unmanagedSourceDirectories += baseDirectory.value / "build/bbc/equivalence-current/sources"',
                "runMain leet.bbc.GenerateCurrentEquivalence",
            ],
            cwd=ROOT,
            stdout=out,
            stderr=subprocess.STDOUT,
            check=True,
            timeout=300,
        )
    for _, path, _ in parents():
        cpu_events.inline_file(path)
    if before != generation_inputs() or baseline != hashes(frozen):
        raise ValueError("Sources or existing simulation inputs changed during generation")
    report = {
        "passed": True,
        "inputs_unchanged": True,
        "inputs": before,
        "artifacts": generation_outputs(),
        "baseline_preserved": baseline,
    }
    write_json(DIRECTORY / "generation.json", report)
    for m, _, _ in parents():
        write_json(DIRECTORY / f"generated{m}" / "elaboration.json", report)
    return audit()


def qualify():
    from tools.bbc.proofs.cpu_reference import callback_order, explicit_events

    generation = generate()
    event_path = explicit_events.DIRECTORY / "result.json"
    event_cpu = memory_composition.sealed(event_path)
    for path in cpu_events.files():
        if event_cpu["inputs"].get(str(path.relative_to(ROOT))) != generation["inputs"].get(
            str(path.relative_to(ROOT))
        ):
            raise ValueError("CPU event proof and parent use different implementations")
    for _, path, _ in parents():
        cpu_events.strip(path.read_text())
    for _, path, top in parents():
        callback_order.fdc_instance(path.read_text(), top)
    for m, path, _ in parents():
        machine_clock_proof.qualify(m, source=path, directory=DIRECTORY / f"clock{m}")
    cpu_request_deadline.qualify(sources=parents(), directory=DIRECTORY / "request-spacing")
    memory_deadline.qualify(sources=parents(), directory=DIRECTORY / "response-deadline")
    adc_scheduler.qualify(parents=parents(), directory=DIRECTORY / "adc-scheduler")
    memory_binding_proof.qualify(sources=parents(), directory=DIRECTORY / "memory-routing")
    rom_proof.qualify(sources=parents(), directory=DIRECTORY / "rom")
    startup_interrupts.qualify(sources=parents(), directory=DIRECTORY / "startup-interrupts")
    paths = [DIRECTORY / f"clock{m}" / "result.json" for m, _, _ in parents()] + [
        DIRECTORY / n / "result.json"
        for n in (
            "request-spacing",
            "response-deadline",
            "adc-scheduler",
            "memory-routing",
            "rom",
            "startup-interrupts",
        )
    ]
    for path in paths:
        memory_composition.sealed(path)
    cpu_path = build.BBC / "cpu-source-reset-rtl/result.json"
    display_path = build.BBC / "display-source-history/result.json"
    crtc_path = build.BBC / "crtc-source-history/result.json"
    warm_path = build.BBC / "cpu-source-warm-startup-rtl/result.json"
    cpu, display, warm, crtc = [
        memory_composition.sealed(p) for p in (cpu_path, display_path, warm_path, crtc_path)
    ]
    for m, path, top in parents():
        parent = bus_binding_proof.module(path.read_text(), top)
        bindings = bus_binding_proof.ports(parent, "BbcCpu", "cpu")
        for port, value in (
            ("clk", "clk"),
            ("reset", "reset"),
            ("enable", "cpuEnable"),
            ("warm_reset", "io_warmReset"),
        ):
            if bindings[port].strip() != value:
                raise ValueError("Changed actual CPU acceptance binding")
        if (
            display["inputs"].get(str(path.relative_to(ROOT)))
            != generation["artifacts"][str(path.relative_to(ROOT))]
        ):
            raise ValueError("Display contract binds a different machine")
        if (
            crtc["inputs"].get(str(path.relative_to(ROOT)))
            != generation["artifacts"][str(path.relative_to(ROOT))]
        ):
            raise ValueError("CRTC source contract binds a different machine")
    for report in (cpu, warm):
        for key in ("rtl/bbc/BbcCpu.v", "build/bbc/ArletCpuEnabled.v"):
            if report["inputs"][key] != generation["inputs"][key]:
                raise ValueError("CPU proof and elaboration use different implementations")
    if generation != audit():
        raise ValueError("Current generation changed during integration checks")
    inputs = [
        Path(__file__),
        event_path,
        *paths,
        cpu_path,
        display_path,
        warm_path,
        crtc_path,
        DIRECTORY / "generation.json",
    ]
    before = hashes(inputs)
    for p in (*paths, cpu_path, display_path, warm_path, crtc_path, event_path):
        memory_composition.sealed(p)
    stable = before == hashes(inputs)
    write_json(
        DIRECTORY / "result.json",
        {
            "passed": stable,
            "inputs_unchanged": stable,
            "inputs": before,
            "artifacts": hashes([DIRECTORY / "generation.json"]),
            "premises": [
                str(p.relative_to(ROOT))
                for p in (*paths, cpu_path, display_path, warm_path, crtc_path, event_path)
            ],
            "scope": "Current complete 32/96 MHz elaborations, actual CPU/clock/warm-reset bindings, request spacing, memory routing, verified ROM contents/capture, RAM capture deadlines, read-only startup interrupt quiescence and ADC relative scheduling. Source/RTL cold and warm startup, source-derived local display histories and the 90-bit original-JS CRTC character projection bind these same implementations. Existing simulator binaries and images are preserved.",
            "limits": "This connects local CPU/video/scheduler premises to current parents; it does not discharge all memory/device values, post-startup warm/interrupt histories, original CRTC/global phase or whole-game equivalence.",
            "universal_game_equivalence": False,
        },
    )
    if not stable:
        raise ValueError("Integration evidence changed")
    print("Current machine integration: PASS", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--generate-only", action="store_true")
    args = parser.parse_args()
    generate() if args.generate_only else qualify()
