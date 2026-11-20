"""Compare Elite-A replays on BBC RTL and the software reference."""

from __future__ import annotations

import argparse
import datetime
import html
import json
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed, wait
from functools import partial
from pathlib import Path

from tools.validation.common import (
    HERE,
    ROOT,
    artifact_hashes,
    digest,
    publish_latest,
    reference_symbols,
    source_root,
    write_json,
)
from tools.validation.compare import field
from tools.validation.run import compile_scenario
from tools.validation.setup import verify

from . import boot, boot_cache, media
from .build import native_binary, native_current, native_cwd
from .capture import capture_pair, native_environment, run, scenario_command
from .compare import compare, game_state
from .host_budget import capacity as host_capacity
from .inputs import analogue_transitions, transitions, write_schedule

BBC = ROOT / "build/bbc"


def compile_selected(contract, names=None):
    """Compile and check the selected scenarios before booting."""
    selected = [s for s in contract["scenarios"] if not names or s["name"] in names]
    if not selected or (names and set(names) - {s["name"] for s in selected}):
        raise ValueError("Unknown scenario")
    compiled = []
    for raw in selected:
        try:
            preset = raw.get("media_fixture")
            if preset is not None and (not isinstance(preset, str) or preset not in media.PRESETS):
                raise ValueError("Unknown commander media preset")
            scenario = compile_scenario(raw, contract["actions"])
            analogue_transitions(scenario)
        except (KeyError, ValueError) as error:
            raise ValueError(f"Invalid scenario {raw['name']}: {error}") from error
        # These fields describe the PicoRV32 demo.
        scenario["unsupported_actions"] = []
        scenario["dut_assertions"] = []
        compiled.append(scenario)
    return compiled


def validate_outcomes(scenarios):
    """Reject unknown outcome fields after asset verification, before backend work."""
    if not any(s.get("reference_assertions") for s in scenarios):
        return
    # Decode both overlays from a scratch buffer for field validation.
    ram = bytearray(65536)
    states = [game_state(ram)]
    ram[reference_symbols("flight")["QQ12"]] = 128
    states.append(game_state(ram))
    for scenario in scenarios:
        checkpoints = {cp["name"] for cp in scenario["checkpoints"]}
        for assertion in scenario.get("reference_assertions", []):
            if (
                not isinstance(assertion, dict)
                or assertion.get("checkpoint") not in checkpoints
                or not isinstance(assertion.get("field"), str)
                or not assertion["field"]
                or sum(key in assertion for key in ["equals", "at_least"]) != 1
            ):
                raise ValueError(f"Invalid outcome assertion in {scenario['name']}: {assertion}")
            for state in states:
                try:
                    field(state, assertion["field"])
                    break
                except (LookupError, TypeError, ValueError):
                    pass
            else:
                raise ValueError(
                    f"Unknown outcome field in {scenario['name']}: {assertion['field']}"
                )


def capture_matrix(scenarios, repetitions, jobs, capture, *, on_progress=None, recovered=None):
    """Start long scenarios first and return results in scenario order.

    Repetitions share read-only boot files. The backend pool bounds child processes.
    """
    recovered = recovered or {}
    results = [
        [recovered.get((scenario["name"], index)) for index in range(repetitions)]
        if recovered
        else [None] * repetitions
        for scenario in scenarios
    ]
    if on_progress:
        for row in results:
            if all(item is not None for item in row):
                on_progress(row)
    work = sorted(
        (
            (index, repetition, scenario)
            for index, scenario in enumerate(scenarios)
            for repetition in range(repetitions)
            if results[index][repetition] is None
        ),
        key=lambda item: (-item[2]["duration_us"], item[0], item[1]),
    )
    with (
        ThreadPoolExecutor(max_workers=jobs) as backends,
        ThreadPoolExecutor(max_workers=jobs) as captures,
    ):
        pending = {
            captures.submit(capture, scenario, repetition, backends): (index, repetition)
            for index, repetition, scenario in work
        }
        for future in as_completed(pending):
            index, repetition = pending[future]
            results[index][repetition] = future.result()
            if on_progress and all(item is not None for item in results[index]):
                on_progress(results[index])
    return results


def summarize_repetitions(repetitions):
    """Report every repetition's outcome and retain the first capture's details."""
    if not repetitions:
        raise ValueError("A scenario requires at least one repetition")
    result = dict(repetitions[0])
    repeatable = (
        len(repetitions) == 2
        and "artifact_hashes" in repetitions[0]
        and repetitions[0]["artifact_hashes"] == repetitions[1].get("artifact_hashes")
    )
    result["repeatable"] = repeatable
    result["passed"] = all(item["passed"] for item in repetitions) and (
        len(repetitions) == 1 or repeatable
    )
    result["repetitions"] = [
        {key: item[key] for key in ["passed", "error"] if key in item} for item in repetitions
    ]
    return result


def matrix_evidence(results, expected, *, boot_provenance_complete, inputs_unchanged):
    """Derive coverage from uniquely named, repeatable scenario results."""
    by_name = {result["name"]: result for result in results}
    expected = set(expected)
    checks = {
        "scenario_coverage": {
            "passed": bool(expected) and set(by_name) == expected and len(by_name) == len(results),
            "missing": sorted(expected - by_name.keys()),
            "unexpected": sorted(by_name.keys() - expected),
            "duplicate_names": len(by_name) != len(results),
        },
        "comparisons": {
            "passed": bool(results) and all(result["passed"] is True for result in results),
            "failed": [result["name"] for result in results if result["passed"] is not True],
        },
        "repeatability": {
            "passed": bool(results) and all(result["repeatable"] is True for result in results),
            "unverified": [
                result["name"] for result in results if result["repeatable"] is not True
            ],
        },
        "boot_provenance": {"passed": boot_provenance_complete is True},
        "input_provenance": {"passed": inputs_unchanged is True},
    }
    return {"passed": all(check["passed"] for check in checks.values()), **checks}


def verified_boots(contexts, snapshots):
    """Bind the boots actually selected before capture through its final guard."""
    try:
        return all(
            boot_cache.valid(context=context)
            and snapshots[name or "released"]["hashes"]
            == {
                str(p): digest(p)
                for states in boot_cache.backends(context).values()
                for p in states
            }
            for name, context in contexts.items()
        )
    except (OSError, ValueError):
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", action="append")
    parser.add_argument(
        "--clock-multiplier",
        type=int,
        choices=[1, 3],
        default=1,
        help="Actual 32 or 96 MHz DUT master clock; fixture units remain 32 MHz",
    )
    parser.add_argument("--repeat", type=int, choices=[1, 2], default=2)
    parser.add_argument(
        "--matrix-only",
        action="store_true",
        help="Exit successfully when all scenarios pass twice with verified boot and input provenance",
    )
    parser.add_argument(
        "--host-timeout-factor",
        type=int,
        choices=range(1, 9),
        default=1,
        help="Scale backend wall-time limits on busy hosts; guest clocks and observations are unchanged",
    )
    parser.add_argument(
        "--profile96", action="store_true", help="Use the separately verified 96 MHz PGO simulator"
    )
    reuse_options = parser.add_mutually_exclusive_group()
    reuse_options.add_argument(
        "--reference-run",
        type=Path,
        help="Reuse unchanged original observations from passing cases in a frozen run; execute every DUT case anew",
    )
    reuse_options.add_argument(
        "--reuse-pairs-from",
        type=Path,
        help="Recover verified completed repetitions from a prior run with identical inputs; replay the rest",
    )
    parser.add_argument(
        "--reuse-boots-from",
        type=Path,
        help="Restore verified own-machine boots from an isolated run before executing fresh scenarios",
    )
    parser.add_argument(
        "--jobs",
        type=int,
        default=host_capacity(),
        help="Capture process limit (default: host affinity/container quota); each repetition owns its disk and captures",
    )
    parser.add_argument(
        "--reference-cpu",
        choices=["jsbeeb", "cycle"],
        default="cycle",
        help="Instruction-batched original CPU or separately qualified cycle-stepped NMOS core",
    )
    parser.add_argument(
        "--drive-profile",
        choices=["original-spindle", "constant-fm", "clocked-fm"],
        default="clocked-fm",
        help="Original spindle, constant 300 rpm, or exact 64 us FM byte clock fixture (default)",
    )
    parser.add_argument(
        "--power-profile",
        choices=["original", "shared-reset"],
        default="shared-reset",
        help="Original constructor state or common declared 8 us board reset and muted PSG startup",
    )
    parser.add_argument(
        "--adc-profile",
        choices=["original", "bbc-result-bits"],
        default="bbc-result-bits",
        help="Pinned original ADC or independently checked completed-result bit correction",
    )
    parser.add_argument(
        "--reuse-development-boot",
        action="store_true",
        help="Use existing own-machine snapshots; final provenance remains incomplete",
    )
    parser.add_argument(
        "--fresh-boot",
        action="store_true",
        help="Ignore verified own-machine boot cache and replay original ROM/game boot",
    )
    parser.add_argument(
        "--trace-bus", action="store_true", help="Record passive MMIO write timing on both backends"
    )
    parser.add_argument(
        "--raw-captures",
        action="store_true",
        help="Retain raw digital/RAM/RGB/disc observations instead of verified lossless storage",
    )
    args = parser.parse_args()
    if args.jobs < 1:
        parser.error("--jobs must be positive")
    if args.reuse_pairs_from and (args.repeat != 2 or args.reuse_development_boot):
        parser.error("--reuse-pairs-from requires two repetitions and verified own-machine boots")
    if args.reuse_boots_from and (args.fresh_boot or args.reuse_development_boot):
        parser.error("--reuse-boots-from requires the verified boot cache")
    if args.profile96 and args.clock_multiplier != 3:
        parser.error("--profile96 requires --clock-multiplier 3")
    if args.reference_run and args.raw_captures:
        parser.error(
            "--reference-run requires verified lossless capture storage; omit --raw-captures"
        )
    contract = json.loads((HERE / "scenarios.json").read_text())
    extra = json.loads((ROOT / "tools/bbc/scenarios.json").read_text())
    contract["actions"].update(extra["actions"])
    contract["scenarios"].extend(extra["scenarios"])
    selected = compile_selected(contract, args.scenario)
    verification = verify()
    validate_outcomes(selected)
    for scenario in selected:
        transitions(scenario)
    boot_cache.configure(
        args.reference_cpu,
        args.drive_profile,
        args.power_profile,
        args.adc_profile,
        clock_multiplier=args.clock_multiplier,
    )
    reference_options = {
        "reference_cpu": args.reference_cpu,
        "drive_profile": args.drive_profile,
        "power_profile": args.power_profile,
        "adc_profile": args.adc_profile,
    }
    if args.adc_profile == "bbc-result-bits":
        from . import adc_results

        adc_results.prepare()
    if args.reference_cpu == "cycle":
        from . import cycle_reference

        cycle_reference.prepare()
        reference_options["cycle_library"] = str(cycle_reference.BINARY)

    def current_native():
        if args.profile96:
            from . import profile96

            return profile96.current()
        return (
            native_current()
            if args.clock_multiplier == 1
            else native_current(args.clock_multiplier)
        )

    if not current_native():
        raise ValueError("BBC simulator is stale or unverified; run make bbc-build")
    if args.profile96:
        from . import profile96

        binary = profile96.BINARY
        optimized_files = [
            profile96.RECEIPT,
            profile96.BINARY,
            *profile96.DATA.glob("*.gcda"),
            *[
                path
                for case in json.loads(profile96.RECEIPT.read_text())["verification"]
                for model in ("baseline", "optimized")
                for path in (profile96.DIRECTORY / case["name"] / model / "capture").iterdir()
                if path.is_file()
            ],
        ]
    else:
        binary, optimized_files = native_binary(args.clock_multiplier), []
    now = datetime.datetime.now(datetime.UTC).strftime("%Y%m%dT%H%M%SZ")
    (BBC / "runs").mkdir(exist_ok=True)
    output = Path(tempfile.mkdtemp(prefix=now + "-", dir=BBC / "runs"))
    contexts = {
        name: None
        if name is None
        else boot_cache.media_profile(media.prepare(name), reference_options)
        for name in dict.fromkeys(s.get("media_fixture") for s in selected)
    }
    declared_media = {
        name: json.loads(context.disc.with_suffix(".json").read_text())
        for name, context in contexts.items()
        if context is not None
    }

    def input_files():
        return [
            *ROOT.glob("rtl/bbc/*"),
            ROOT / "rtl/audio/Psg.scala",
            *ROOT.glob("third_party/6502/*.v"),
            *ROOT.glob("tools/bbc/*.py"),
            *ROOT.glob("tools/bbc/*.mjs"),
            ROOT / "tools/bbc/scenarios.json",
            ROOT / "sim/native/bbc.cpp",
            ROOT / "sim/native/bbc_audio.h",
            ROOT / "sim/native/bbc_frame_hash.h",
            ROOT / "tools/validation/reference_memory.mjs",
            ROOT / "tools/validation/reference_timing.mjs",
            HERE / "reference.lock.json",
            HERE / "scenarios.json",
            *[HERE / name for name in ["common.py", "compare.py", "run.py", "setup.py"]],
            *ROOT.glob("tools/bbc/reference/*"),
            *(
                [*adc_results.files(), adc_results.RECEIPT, adc_results.BINARY, adc_results.CORPUS]
                if args.adc_profile == "bbc-result-bits"
                else []
            ),
            BBC / ("BbcMachine.v" if args.clock_multiplier == 1 else "full-video/BbcMachine96.v"),
            *(
                [
                    ROOT / "sim/native/bbc_machine96.cpp",
                    BBC / "full-video/elaboration.json",
                    BBC / "full-video/native/build.json",
                    *BBC.glob("full-video/*.v_*.bin"),
                ]
                if args.clock_multiplier == 3
                else []
            ),
            BBC / "ArletCpuEnabled.v",
            native_binary(args.clock_multiplier),
            *optimized_files,
            BBC / "boot.keys",
            BBC / "rom.hex",
            BBC / "disc.ssd",
            *BBC.glob("Bbc*.v_*.bin"),
            *[
                path
                for context in contexts.values()
                if context is not None
                for path in [context.disc, context.disc.with_suffix(".json")]
            ],
        ]

    files = input_files()
    if args.reference_cpu == "cycle":
        files.extend([cycle_reference.BINARY, cycle_reference.RECEIPT])
    provenance = {str(p.relative_to(ROOT)): digest(p) for p in sorted(files) if p.is_file()}
    boot_source = args.reuse_boots_from or args.reuse_pairs_from
    manifest = {
        "target": "Elite-A released, Model B DFS1.2, no Tube",
        "clock_hz": 32000000,
        "master_clock_hz": 32000000 * args.clock_multiplier,
        "scenario_workers": args.jobs,
        "scenarios": [scenario["name"] for scenario in selected],
        "repetitions": args.repeat,
        "capture_process_limit": args.jobs,
        "host_timeout_factor": args.host_timeout_factor,
        "capture_storage": "raw" if args.raw_captures else "verified-gzip",
        "trace_bus": args.trace_bus,
        "compiler_profile": "verified-96-pgo" if args.profile96 else "qualified-native",
        "reference_options": reference_options,
        "reference_observations_source": str(args.reference_run.resolve())
        if args.reference_run
        else None,
        "reused_pairs_source": str(args.reuse_pairs_from.resolve())
        if args.reuse_pairs_from
        else None,
        "boot_cache_source": str(boot_source.resolve())
        if boot_source and not args.fresh_boot
        else None,
        "inputs": provenance,
        "reference_verification": verification,
        "input_media": declared_media,
    }
    write_json(output / "manifest.json", manifest)
    if boot_source:
        from . import pair_reuse

    if args.reuse_pairs_from:
        source, old_manifest = pair_reuse.verify_source(args.reuse_pairs_from, manifest)
    if boot_source and not args.fresh_boot:
        pair_reuse.recover_boots(boot_source, contexts, reference_options)
    with (
        ThreadPoolExecutor(max_workers=args.jobs) as backends,
        ThreadPoolExecutor(max_workers=len(contexts)) as profiles,
    ):
        pending = {
            name: profiles.submit(
                boot.prepare,
                output if name is None else output / "boots" / name,
                reference_options,
                context=context,
                fresh=args.fresh_boot,
                development=args.reuse_development_boot,
                jobs=args.jobs,
                executor=backends,
                optimized96=args.profile96,
            )
            for name, context in contexts.items()
        }
        wait(pending.values())
        snapshots = {name or "released": future.result() for name, future in pending.items()}
    # Retain the canonical-only schema consumed by historical capture tools.
    snapshot = snapshots.get("released", next(iter(snapshots.values())))
    write_json(output / "boot-snapshots.json", {**snapshot, "media_profiles": snapshots})

    def prepare_capture(scenario, repetition, parent):
        case_name = scenario["name"] + ("-repeat" if repetition else "")
        directory = parent / case_name
        directory.mkdir()
        write_schedule(directory, scenario, analogue=True)
        context = contexts[scenario.get("media_fixture")]
        states = boot_cache.backends(context)
        config = {
            **reference_options,
            "jsbeeb": str(source_root("jsbeeb")),
            "disc": str(boot_cache.disc_path(context)),
            "load_state": str(states["reference"][0]),
            "output": str(output / case_name / "reference"),
            "scenario": scenario,
            "trace_bus": args.trace_bus,
            "compressed_float_audio": True,
        }
        write_json(directory / "scenario.json", scenario)
        write_json(directory / "reference.json", config)
        return directory, config

    def capture(scenario, repetition, backend_executor):
        print(f"Capturing {scenario['name']} ({repetition + 1}/{args.repeat})", flush=True)
        directory, config = prepare_capture(scenario, repetition, output)
        context = contexts[scenario.get("media_fixture")]
        states = boot_cache.backends(context)
        try:
            def reference_capture():
                if args.reference_run:
                    from . import reference_reuse

                    reference_reuse.reuse(args.reference_run, directory.name, config, context)
                else:
                    run(
                        [
                            "node",
                            str(ROOT / "tools/bbc/reference.mjs"),
                            str(directory / "reference.json"),
                        ],
                        ROOT,
                        directory / "reference.log",
                        timeout=max(600, scenario["duration_us"] // 25000)
                        * args.host_timeout_factor,
                        pack=None if args.raw_captures else directory / "reference",
                    )

            env = native_environment(
                load=states["dut"][0], trace=args.trace_bus, analogue=directory / "analogue.tsv"
            )
            capture_pair(
                backend_executor,
                partial(
                    run,
                    scenario_command(binary, directory, scenario, "dut"),
                    native_cwd(args.clock_multiplier),
                    directory / "dut.log",
                    env,
                    timeout=max(600, scenario["duration_us"] // 25000)
                    * args.clock_multiplier
                    * args.host_timeout_factor,
                    pack=None if args.raw_captures else directory / "dut",
                ),
                reference_capture,
            )
            result = compare(directory, scenario)
            result["artifact_hashes"] = artifact_hashes(directory)
        except (LookupError, ValueError, subprocess.SubprocessError, OSError) as error:
            result = {"name": scenario["name"], "passed": False, "error": str(error)}
        pending = directory / "comparison.tmp"
        write_json(pending, result)
        pending.replace(directory / "comparison.json")
        print(
            f"{scenario['name']} ({repetition + 1}/{args.repeat}): {'PASS' if result['passed'] else 'FAIL'}",
            flush=True,
        )
        return result

    completed = []

    def record_progress(repetitions):
        result = summarize_repetitions(repetitions)
        print(f"{result['name']}: {'PASS' if result['passed'] else 'FAIL'}", flush=True)
        completed.append(
            {key: result[key] for key in ("name", "passed", "repeatable", "repetitions")}
        )
        progress = output / "progress.json"
        pending = progress.with_suffix(".tmp")
        write_json(
            pending,
            {
                "expected_scenarios": len(selected),
                "completed_scenarios": len(completed),
                "passed_scenarios": sum(row["passed"] for row in completed),
                "results": completed,
            },
        )
        pending.replace(progress)

    recovered = {}
    if args.reuse_pairs_from:
        available = pair_reuse.completed(source, old_manifest, snapshots)
        recovered = pair_reuse.recover(
            source, output, selected, available, prepare_capture, min(args.jobs, host_capacity())
        )
    reused_repetitions = {}
    for name, index in sorted(recovered):
        reused_repetitions.setdefault(name, []).append(index + 1)
    captured = capture_matrix(
        selected, args.repeat, args.jobs, capture, on_progress=record_progress, recovered=recovered
    )
    results = [summarize_repetitions(row) for row in captured]
    remaining = [
        "Analogue sound and physical chip acceptance phase",
        "DFS save/load pixel, sound and timing equivalence",
        "Hyperspace world/timing parity",
        "Further combat, delivery variants, ship and galaxy coverage, economic boundaries",
        "Teletext and interlaced video modes",
        "VIA coincident serial/mode transitions and physical VIA/light-pen pin phase",
        "ADC analogue-dependent duration/sampling and coincident-read races, physical storage/video/audio board integration and routed timing",
    ]
    if any(r["name"] == "dfs-roundtrip" and r["passed"] and r["repeatable"] for r in results):
        remaining.remove("DFS save/load pixel, sound and timing equivalence")
    if any(r["name"] == "hyperspace-diso" and r["passed"] and r["repeatable"] for r in results):
        remaining.remove("Hyperspace world/timing parity")
    complete_matrix = len(selected) == len(contract["scenarios"])
    if not (
        complete_matrix
        and all(
            r.get("audio", {}).get("digital_waveform", {}).get("passed") and r["repeatable"]
            for r in results
        )
    ):
        remaining.insert(0, "Digital sound waveform equivalence across the complete replay matrix")
    final_files = input_files()
    if args.reference_cpu == "cycle":
        final_files.extend([cycle_reference.BINARY, cycle_reference.RECEIPT])
    inputs_unchanged = (
        provenance
        == {str(p.relative_to(ROOT)): digest(p) for p in sorted(final_files) if p.is_file()}
        and current_native()
        and (args.reference_cpu != "cycle" or cycle_reference.current())
        and (args.adc_profile != "bbc-result-bits" or adc_results.current())
    )
    reference_reuse_unchanged = True
    if args.reference_run:
        receipts = list(output.glob("*/reference-reuse.json"))
        try:
            guarded = {}
            for receipt in receipts:
                for name, value in json.loads(receipt.read_text())["source_guards"].items():
                    if name in guarded and guarded[name] != value:
                        raise ValueError("Original input changed between reused cases")
                    guarded[name] = value
            reference_reuse_unchanged = len(receipts) == len(selected) * args.repeat and all(
                digest(Path(name)) == value for name, value in guarded.items()
            )
        except (OSError, ValueError):
            reference_reuse_unchanged = False
    inputs_unchanged = inputs_unchanged and reference_reuse_unchanged
    boot_provenance_complete = not args.reuse_development_boot and verified_boots(
        contexts, snapshots
    )
    matrix = matrix_evidence(
        results,
        (scenario["name"] for scenario in contract["scenarios"]),
        boot_provenance_complete=boot_provenance_complete,
        inputs_unchanged=inputs_unchanged,
    )
    strict_matrix_passed = matrix["passed"]
    passed = strict_matrix_passed and not remaining
    report = {
        "passed": passed,
        "strict_matrix_passed": strict_matrix_passed,
        "matrix_evidence": matrix,
        "results": results,
        "remaining": remaining,
        "reference_options": reference_options,
        "master_clock_hz": 32000000 * args.clock_multiplier,
        "boot_provenance_complete": boot_provenance_complete,
        "inputs_unchanged": inputs_unchanged,
        "reference_reuse_unchanged": reference_reuse_unchanged,
        "reused_pairs": sorted(name for name, reps in reused_repetitions.items() if len(reps) == 2),
        "reused_repetitions": reused_repetitions,
    }
    write_json(output / "report.json", report)
    rows = []
    for result in results:
        details = html.escape(
            json.dumps({k: v for k, v in result.items() if k != "artifact_hashes"}, indent=2)
        )
        rows.append(f"<h2>{html.escape(result['name'])}</h2><pre>{details}</pre>")
    (output / "report.html").write_text(
        '<!doctype html><meta charset="utf-8"><title>Original Elite-A on BBC RTL</title><h1>Full equivalence: '
        + ("PASS" if passed else "INCOMPLETE")
        + "</h1>"
        + "<h2>Strict repeated scenario matrix: "
        + ("PASS" if strict_matrix_passed else "INCOMPLETE")
        + "</h2><pre>"
        + html.escape(json.dumps(matrix, indent=2))
        + "</pre>"
        + "".join(rows)
        + "<h2>Remaining coverage</h2><pre>"
        + html.escape("\n".join(remaining))
        + "</pre>"
    )
    publish_latest(BBC / "latest", output)
    print("Report: " + str(output / "report.html"), flush=True)
    return 0 if (strict_matrix_passed if args.matrix_only else passed) else 1


if __name__ == "__main__":
    raise SystemExit(main())
