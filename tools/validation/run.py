"""Replay released Elite-A and the real Soc; nonzero on mismatches or gaps."""

from __future__ import annotations

import argparse
import datetime
import json
import subprocess
import sys
import tempfile
from pathlib import Path

from .blueprints import extract_blueprints
from .common import (
    CACHE,
    HERE,
    LOCK,
    ROOT,
    artifact_hashes,
    digest,
    publish_latest,
    firmware_symbols,
    reference_symbols,
    source_root,
    write_json,
)
from .compare import compare_scenario, report
from .psg import run_probes
from .render import verified_result
from .setup import prepare


def compile_scenario(scenario: dict, actions: dict) -> dict:
    result = json.loads(json.dumps(scenario))
    events = [{"at_us": 0, "keys": [], "input": 0, "input2": 0}]
    unsupported = []
    last = 0
    for event in result["events"]:
        at = event["at_us"]
        if not isinstance(at, int) or at <= last or at >= result["duration_us"]:
            raise ValueError("Input events must have increasing integer guest times")
        last = at
        keys = []
        word = word2 = 0
        for name in event["actions"]:
            action = actions[name]
            keys.extend(action["keys"])
            word |= action.get("input", 0)
            word2 |= action.get("input2", 0)
            if "unsupported" in action:
                unsupported.append({"action": name, "at_us": at, "reason": action["unsupported"]})
        events.append(
            {
                "at_us": at,
                "keys": sorted(set(keys)),
                "input": word,
                "input2": word2,
                "actions": event["actions"],
            }
        )
    if events[-1]["keys"] or events[-1]["input"] or events[-1]["input2"]:
        raise ValueError("Scenario must explicitly release held inputs")
    names = set()
    last = -1
    for cp in result["checkpoints"]:
        if cp["name"] in names or cp["at_us"] <= last or cp["at_us"] >= result["duration_us"]:
            raise ValueError("Invalid checkpoint schedule")
        names.add(cp["name"])
        last = cp["at_us"]
    if not names:
        raise ValueError("Scenario needs state/video checkpoints")
    result["events"] = events
    result["unsupported_actions"] = unsupported
    return result


def capture(directory: Path, scenario: dict) -> None:
    directory.mkdir(parents=True)
    write_json(directory / "scenario.json", scenario)
    config = {
        **scenario,
        "jsbeeb": str(source_root("jsbeeb")),
        "disc": str(source_root("elite_a") / "5-compiled-game-discs/elite-a-released.ssd"),
        "output": str(directory / "reference"),
    }
    write_json(directory / "reference-config.json", config)
    (directory / "events.tsv").write_text(
        "".join(f"{e['at_us'] * 4}\t{e['input']}\t{e['input2']}\n" for e in scenario["events"])
    )
    (directory / "checkpoints.tsv").write_text(
        "".join(f"{e['at_us'] * 4}\t{e['name']}\n" for e in scenario["checkpoints"])
    )
    commands = [
        (
            ["node", str(HERE / "reference.mjs"), str(directory / "reference-config.json")],
            ROOT,
            "reference.log",
        ),
        (
            [
                str(ROOT / "build/rtl/obj_validate/validate"),
                str(directory / "events.tsv"),
                str(directory / "checkpoints.tsv"),
                str(scenario["duration_us"] * 4),
                str(directory / "dut"),
            ],
            ROOT / "build/rtl",
            "dut.log",
        ),
    ]
    for command, cwd, log in commands:
        with (directory / log).open("w") as output:
            subprocess.run(
                command, cwd=cwd, stdout=output, stderr=subprocess.STDOUT, timeout=240, check=True
            )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--scenario",
        action="append",
        help="Run only named scenarios; coverage remains explicitly incomplete",
    )
    parser.add_argument(
        "--repeat",
        type=int,
        choices=[1, 2],
        default=2,
        help="Default: independently capture every scenario twice",
    )
    args = parser.parse_args()
    prepare()
    contract = json.loads((HERE / "scenarios.json").read_text())
    chosen = [s for s in contract["scenarios"] if not args.scenario or s["name"] in args.scenario]
    if not chosen or (args.scenario and set(args.scenario) - {s["name"] for s in chosen}):
        parser.error("Unknown or empty scenario selection")
    if not (ROOT / "build/rtl/obj_validate/validate").is_file():
        parser.error("Build first with make validation-build")
    runroot = CACHE / "runs"
    runroot.mkdir(parents=True, exist_ok=True)
    directory = Path(
        tempfile.mkdtemp(
            prefix=datetime.datetime.now(datetime.UTC).strftime("%Y%m%dT%H%M%SZ-"),
            dir=runroot,
        )
    )
    refsyms, dutsyms = reference_symbols(), firmware_symbols()
    blueprints = extract_blueprints(directory / "reference-blueprints.json")
    manifest = {
        "reference": LOCK,
        "reference_verification": json.loads((CACHE / "reference-verification.json").read_text()),
        "dut_symbols": dutsyms,
        "reference_symbols": refsyms,
        "repeat": args.repeat,
        "dut_clock_basis": "Declared 4 MHz simulation clock; no board frequency claim",
        "baseline": "Authentic released NEWCOME/Adder commander docked at Lave; DUT first frame in space",
        "reference_blueprints": {
            "ship_names": blueprints["ship_names"],
            "geometry_variants": blueprints["geometry_variants"],
            "instances": blueprints["instances"],
            "external_runtime_geometry": blueprints["external_runtime_geometry"],
        },
        "tool_versions": {},
        "input_hashes": {},
    }
    for tool, command in {
        "node": ["node", "--version"],
        "verilator": ["verilator", "--version"],
        "riscv_nm": ["riscv64-unknown-elf-nm", "--version"],
    }.items():
        manifest["tool_versions"][tool] = subprocess.run(
            command, capture_output=True, text=True, check=True
        ).stdout.splitlines()[0]
    for path in [
        ROOT / "build/fw/firmware.elf",
        ROOT / "build/fw/firmware.hex",
        ROOT / "build/rtl/Soc.v",
        ROOT / "build/rtl/obj_validate/validate",
        ROOT / "build/rtl/Psg.v",
        ROOT / "build/rtl/obj_validate_psg/validate_psg",
        ROOT / "fw/main.c",
        *ROOT.glob("fw/*.h"),
        ROOT / "sim/native/validate.cpp",
        ROOT / "sim/native/validate_psg.cpp",
        *ROOT.glob("rtl/**/*.scala"),
        *HERE.glob("*.py"),
        *HERE.glob("*.mjs"),
        *HERE.glob("*.c"),
        *HERE.glob("*.json"),
    ]:
        manifest["input_hashes"][str(path.relative_to(ROOT))] = digest(path)
    write_json(directory / "manifest.json", manifest)
    results = []
    for raw in chosen:
        scenario = compile_scenario(raw, contract["actions"])
        name = scenario["name"]
        print(f"Capturing {name}", flush=True)
        try:
            capture(directory / name, scenario)
            result = compare_scenario(directory / name, scenario, refsyms, dutsyms)
            if args.repeat == 2:
                repeated = directory / "repeated" / name
                capture(repeated, scenario)
                compare_scenario(repeated, scenario, refsyms, dutsyms)
                first, second = artifact_hashes(directory / name), artifact_hashes(repeated)
                mismatches = [
                    p for p in first.keys() | second.keys() if first.get(p) != second.get(p)
                ]
                result["repeatability"] = {
                    "passed": not mismatches,
                    "different_artifacts": mismatches,
                    "compared_artifacts": len(first),
                }
                if mismatches:
                    result["passed"] = False
            else:
                result["repeatability"] = {"passed": None, "status": "unrun"}
            write_json(directory / name / "result.json", result)
        except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
            result = {"name": name, "passed": False, "error": str(error)}
            write_json(directory / name / "result.json", result)
        results.append(result)
        print(
            f"{name}: {'PASS' if result['passed'] else 'FAIL'}"
            + (f" ({result['error']})" if "error" in result else ""),
            flush=True,
        )
    print("Probing independent PSG tone/noise behavior", flush=True)
    try:
        probes = run_probes(directory / "psg-probes")
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        probes = {"passed": False, "error": str(error)}
    print(f"PSG probes: {'PASS' if probes['passed'] else 'FAIL'}", flush=True)
    try:
        renderer_directory, renderer = verified_result()
        renderer = {**renderer, "artifact_directory": str(renderer_directory)}
    except (OSError, ValueError) as error:
        renderer = {"passed": False, "error": str(error)}
    write_json(directory / "renderer-kernel.json", renderer)
    print(f"Released renderer kernel: {'PASS' if renderer['passed'] else 'FAIL'}", flush=True)
    gaps = dict(contract["coverage_gaps"])
    if args.scenario:
        gaps["scenario_selection"] = "Only selected scenarios ran"
    if args.repeat != 2:
        gaps["repeatability"] = "Independent repeat capture was not run"
    if probes.get("coverage_gaps"):
        gaps["sound"] = "; ".join(probes["coverage_gaps"])
    if not renderer["passed"]:
        gaps["renderer_validation"] = renderer.get("error", "Renderer kernel validation failed")
    passed = (
        bool(results)
        and all(r["passed"] for r in results)
        and probes["passed"]
        and renderer["passed"]
        and not gaps
    )
    outcome = {
        "target": LOCK["target"],
        "passed": passed,
        "scope": "digital Soc comparison; full Elite-A/board equivalence incomplete",
        "scenarios": results,
        "psg_probes": probes,
        "renderer_kernel": renderer,
        "coverage_gaps": gaps,
    }
    write_json(directory / "result.json", outcome)
    report(directory, results, gaps, probes, passed)
    publish_latest(CACHE / "latest", directory)
    print(f"Report: {directory / 'report.html'}", flush=True)
    print(
        f"Elite-A equivalence: {'PASS' if passed else 'FAIL'} (see comparisons and required coverage)",
        flush=True,
    )
    return 0 if passed else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(f"Validation setup/capture failed: {error}", file=sys.stderr)
        sys.exit(2)
