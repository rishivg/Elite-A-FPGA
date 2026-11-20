"""Compare ordinary whole-machine histories containing digital restart requests."""

import argparse
import gzip
import json
import subprocess
import tempfile
from pathlib import Path

from tools.validation.common import ROOT, source_root, write_json

from . import boot_cache, boot_trace, build, cold_machine_video, crtc_registers, cycle_reference
from .capture import native_environment, run
from .compare import game_state

CYCLES = 32000000
RESTARTS = (12000000, 16000016, 24000000, 28000016)
GAME_CYCLES = 40 * 32000000
GAME_RESTARTS = (22 * 32000000 + 16,)
GAME_POINTS = ((20 * 32000000, "before_restart"), (GAME_CYCLES, "reloaded"))


def digital_outputs(reference, dut, cycles, restart_requests=RESTARTS):
    trace = reference / "video-transactions.tsv"
    ram = boot_trace.compare_events(
        boot_trace.reference_events(trace, {"ram"}), boot_trace.dut_events(dut / "ram-writes.tsv")
    )
    video = boot_trace.compare_events(
        boot_trace.reference_events(trace, {"crtc", "ula"}),
        boot_trace.dut_events(dut / "bus.tsv", video=True),
    )
    with (reference / "cold-mmio.tsv").open() as stream:
        mmio = boot_trace.compare_events(
            ((int(c) * 16, int(a), int(d)) for c, a, d in map(str.split, stream)),
            boot_trace.dut_events(dut / "bus.tsv"),
        )
    sound = boot_trace.compare_events(
        (
            (int(c) * 16, int(d))
            for c, d in map(str.split, (reference / "psg.tsv").read_text().splitlines())
        ),
        (tuple(map(int, row.split())) for row in (dut / "psg.tsv").read_text().splitlines()),
    )
    # Optional event streams may be empty. Both complete captures still need
    # exactly equal counts; a missing required RAM/MMIO stream cannot pass.
    sound_equal = sound["different_events"] == 0
    summary = json.loads((dut / "summary.json").read_text())
    sync = json.loads((reference / "raw-vsync.json").read_text())
    expected_audio = gzip.decompress((reference / "audio.u8.gz").read_bytes())
    audio = (dut / "audio.u8").read_bytes()
    frames = list(map(int, (dut / "frames.tsv").read_text().split()))
    requested = "".join(f"{clock}\n" for clock in restart_requests)
    checks = {
        "ram_writes": ram["passed"],
        "video_writes": video["passed"],
        "mmio_writes": mmio["passed"],
        "sound_events": sound_equal and sound["dut_events"] == summary["psg_writes"],
        "all_audio": audio == expected_audio and len(audio) == cycles // 64 - 4,
        "raw_vsync": frames == [t["cycle"] * 16 for t in sync["transitions"] if t["level"]]
        and len(frames) == summary["frames"],
        "final_ram": (dut / "boot.ram").read_bytes() == (reference / "boot.ram").read_bytes(),
        "restart_inputs": (dut / "restarts.tsv").read_text()
        == (reference / "restarts.tsv").read_text()
        == requested,
        "complete_history": summary["duration_cycles"] == cycles == sync["final_cycle"] * 16
        and summary["restored_own_boot_cycles"] == 0
        and summary.get("capture_scope") == "cold-prefix"
        and summary["completed"] is False
        and sync["observed_audio"] is True,
    }
    return {
        "checks": checks,
        "ram": ram,
        "video": video,
        "mmio": mmio,
        "sound": sound,
        "audio_samples": len(audio),
    }


def original_game_coverage(reference, restart_clock):
    """Reject a failed original reload before starting the long native captures."""
    expected = {
        "commander_name": "NEWCOME",
        "credits_tenths": 5000,
        "fuel_tenths_ly": 60,
        "view": 8,
        "docked": True,
    }
    snapshots = []
    for _, name in GAME_POINTS:
        original = game_state((reference / (name + ".ram")).read_bytes())
        snapshots.append(
            {
                "name": name,
                "reference": original,
                "passed": all(original[k] == v for k, v in expected.items()),
            }
        )
    requests = 0
    for row in (reference / "cold-mmio.tsv").read_text().splitlines():
        clock, address, value = map(int, row.split())
        if (
            clock * 16 > restart_clock
            and 0xFE80 <= address < 0xFEA0
            and address % 8 == 0
            and value & 0x3F in (0x13, 0x17)
        ):
            requests += 1
    metadata = json.loads((reference / "cold-checkpoints.json").read_text())
    points = [{"cycle": cycle, "name": name} for cycle, name in GAME_POINTS]
    checks = {
        "playable_before_and_after_restart": all(p["passed"] for p in snapshots),
        "disc_read_commands_after_restart": requests > 0,
        "all_cold_checkpoints_observed": metadata
        == {"ready": True, "requested": points, "observed": points}
        and metadata.get("ready") is True,
    }
    return {"checks": checks, "snapshots": snapshots, "post_restart_disc_read_commands": requests}


def game_coverage(reference, dut, restart_clock):
    """Require playable own RAM on both sides and actual post-reset disc activity."""
    result = original_game_coverage(reference, restart_clock)
    fields = ("commander_name", "credits_tenths", "fuel_tenths_ly", "view", "docked")
    for point in result["snapshots"]:
        point["dut"] = game_state((dut / (point["name"] + ".ram")).read_bytes())
        point["passed"] = point["passed"] and all(
            point["reference"][k] == point["dut"][k] for k in fields
        )
    result["checks"]["playable_before_and_after_restart"] = all(
        p["passed"] for p in result["snapshots"]
    )
    result["checks"]["all_native_checkpoints_observed"] = json.loads(
        (dut / "summary.json").read_text()
    )["checkpoints"] == len(GAME_POINTS)
    return result


def qualify(profile="rom"):
    if profile not in ("rom", "game"):
        raise ValueError("Unknown restart profile")
    cycles = CYCLES if profile == "rom" else GAME_CYCLES
    restarts = RESTARTS if profile == "rom" else GAME_RESTARTS
    if (
        not all(build.native_current(multiplier) for multiplier in (1, 3))
        or not cycle_reference.current()
    ):
        raise ValueError("Build current whole-machine and independent CPU simulators first")
    directory = Path(tempfile.mkdtemp(prefix=f"machine-restart-{profile}-", dir=build.BBC))
    reference = directory / "reference"
    keys, restart = directory / "keys", directory / "restarts.tsv"
    keys.write_text("")
    checkpoints = "-"
    if profile == "game":
        boot_keys = (build.BBC / "boot.keys").read_text()
        again = "".join(
            f"{int(clock) + restarts[0]} {col} {row} {down}\n"
            for clock, col, row, down in map(str.split, boot_keys.splitlines())
        )
        keys.write_text(boot_keys + again)
        checkpoints = directory / "checkpoints.tsv"
        checkpoints.write_text("".join(f"{clock} {name}\n" for clock, name in GAME_POINTS))
    restart.write_text("".join(f"{clock}\n" for clock in restarts))
    configuration = directory / "reference.json"
    write_json(
        configuration,
        {
            **boot_cache.REFERENCE_OPTIONS,
            "cycle_library": str(cycle_reference.BINARY),
            "jsbeeb": str(source_root("jsbeeb")),
            "disc": str(build.BBC / "disc.ssd"),
            "keys": str(keys),
            "cycles": cycles,
            "restart_events": str(restart),
            "output": str(reference),
            **(
                {
                    "cold_checkpoints": [
                        {"cycle": clock, "name": name} for clock, name in GAME_POINTS
                    ]
                }
                if profile == "game"
                else {}
            ),
        },
    )
    helpers = [
        ROOT / "tools/bbc" / name
        for name in (
            "reference_boot_sync.mjs",
            "reference_boot_video_pins.mjs",
            "reference_boot_video_trace.mjs",
            "reference_boot_bus.mjs",
        )
    ]
    if profile == "game":
        helpers.append(ROOT / "tools/bbc/reference_cold_checkpoints.mjs")
    source = crtc_registers.independent_sources()
    dependencies = [
        Path(__file__),
        *helpers,
        ROOT / "tools/bbc/capture.py",
        ROOT / "tools/bbc/reference.mjs",
        ROOT / "tools/bbc/reference_cycle.mjs",
        ROOT / "tools/bbc/reference_events.mjs",
        cold_machine_video.HEADER,
        ROOT / "sim/native/bbc.cpp",
        configuration,
        keys,
        restart,
        ROOT / "sim/native/bbc_loading_teletext_oracle.c",
        ROOT / "sim/native/bbc_loading_bitmap_oracle.c",
        *[source / name for name in crtc_registers.PIN],
        *(
            [checkpoints, build.BBC / "boot.keys", ROOT / "tools/bbc/compare.py"]
            if profile == "game"
            else []
        ),
    ]

    def inputs():
        return {
            "files": build.hashes(dependencies),
            "dut32": build.native_inputs(1),
            "dut96": build.native_inputs(3),
            "reference": boot_cache.inputs("reference"),
        }

    before = inputs()
    command = ["node"]
    for helper in helpers:
        command.extend(["--import", str(helper)])
    command.extend([str(ROOT / "tools/bbc/reference.mjs"), str(configuration)])
    print("Independent machine restart history: " + str(directory), flush=True)
    run(command, ROOT, directory / "reference.log", timeout=180 if profile == "rom" else 900)
    if profile == "game":
        original_coverage = original_game_coverage(reference, restarts[0])
        write_json(directory / "original-game-coverage.json", original_coverage)
        if not all(original_coverage["checks"].values()):
            raise ValueError("Original did not complete the declared reload: " + str(directory))
    pins = reference / "video-pins.bin.gz"
    cells, bitmap = directory / "teletext-cells.bin.gz", directory / "bitmap-pins.bin.gz"
    for name, driver, libraries, output, arguments in (
        (
            "teletext",
            "bbc_loading_teletext_oracle.c",
            ("util.c", "util_string.c", "log.c"),
            cells,
            [str(pins), str(cells)],
        ),
        (
            "bitmap",
            "bbc_loading_bitmap_oracle.c",
            ("render.c", "teletext.c", "util.c", "util_string.c", "log.c"),
            bitmap,
            [str(pins), str(reference / "video-transactions.tsv"), str(bitmap)],
        ),
    ):
        binary = directory / (name + "-oracle")
        with (directory / (name + "-build.log")).open("w") as log:
            subprocess.run(
                [
                    "cc",
                    "-O2",
                    "-ffunction-sections",
                    "-fdata-sections",
                    "-DBEEBJIT_HEADLESS",
                    "-I" + str(source),
                    str(ROOT / "sim/native" / driver),
                    *[str(source / library) for library in libraries],
                    "-Wl,--gc-sections",
                    "-lm",
                    "-lpthread",
                    "-lz",
                    "-o",
                    str(binary),
                ],
                cwd=ROOT,
                stdout=log,
                stderr=subprocess.STDOUT,
                check=True,
                timeout=120 if profile == "rom" else 900,
            )
        with (directory / (name + "-oracle.log")).open("w") as log:
            subprocess.run(
                [str(binary), *arguments],
                cwd=ROOT,
                stdout=log,
                stderr=subprocess.STDOUT,
                check=True,
                timeout=120,
            )
    metadata = json.loads((reference / "video-pins.json").read_text())
    slots = (cycles - 256) // 16
    if metadata["count"] != slots or metadata["final_cycle"] * 16 != cycles:
        raise ValueError("Independent reset video history is incomplete")
    oracle_files = [pins, cells, bitmap, *reference.glob("*")]
    oracle_before = build.hashes(oracle_files)
    results = []
    for multiplier in (1, 3):
        dut = directory / f"dut{multiplier}"
        env = native_environment(
            trace=True,
            ram_trace=True,
            cold_prefix=True,
            restart=restart,
            disc=build.BBC / "disc.ssd",
        )
        binary = build.native_binary(multiplier)
        compiled = None
        if multiplier == 3:
            binary, compiled = cold_machine_video.compile_observer(directory / "observed96")
            env.update(
                {
                    "BBC_COLD_VIDEO_PINS": str(pins),
                    "BBC_COLD_VIDEO_CELLS": str(cells),
                    "BBC_COLD_VIDEO_BITMAP": str(bitmap),
                }
            )
        run(
            [str(binary), str(cycles), str(keys), str(checkpoints), str(dut)],
            build.native_cwd(multiplier),
            directory / f"dut{multiplier}.log",
            env,
            timeout=240 if profile == "rom" else 7200,
            exit_codes=(1,),
        )
        compared = digital_outputs(reference, dut, cycles, restarts)
        if profile == "game":
            compared["game_coverage"] = game_coverage(reference, dut, restarts[0])
            compared["checks"].update(compared["game_coverage"]["checks"])
        if multiplier == 3:
            observed = json.loads((dut / "cold-video.json").read_text())
            compared["live_video"] = observed
            compared["checks"]["live_rgb_strobe_sync"] = cold_machine_video.complete(
                observed, cycles, True
            )
            compared["checks"]["all_original_dots"] = (
                observed["teletext_dots"] == metadata["teletext_slots"] * 6
                and observed["bitmap_dots"] == (slots - metadata["teletext_slots"]) * 8
            )
        compared["master_clock_hz"] = 32000000 * multiplier
        compared["native_build"] = compiled
        results.append(compared)
        print(f"Machine restart {32000000 * multiplier} Hz: {compared['checks']}", flush=True)
    stable = before == inputs() and oracle_before == build.hashes(oracle_files)
    receipt = {
        "passed": stable and all(all(result["checks"].values()) for result in results),
        "inputs": before,
        "inputs_unchanged": stable,
        "original_observations": oracle_before,
        "profile": profile,
        "requested_cycles": cycles,
        "restart_requests": restarts,
        "results": results,
        "artifacts": build.hashes(
            [
                *reference.glob("*"),
                *directory.glob("*.log"),
                *directory.glob("original-game-coverage.json"),
                cells,
                bitmap,
                *[
                    file
                    for multiplier in (1, 3)
                    for file in (directory / f"dut{multiplier}").glob("*")
                ],
            ]
        ),
        "scope": (
            "Finite one-second ROM history with four restart requests."
            if profile == "rom"
            else "Finite forty-second history: independent Elite-A loading through physical keys, restart after 22 seconds, then retype the released boot sequence and reload. Own playable RAM is observed before and after; post-reset public disc reads are required."
        )
        + " Each actual machine and original starts independently and executes all physical clocks. Compare all RAM/MMIO/video writes, sound events, digital audio, raw VSYNC and final RAM; the 96 MHz machine also compares every live expected RGB dot and pixel/sync phase. No saved state or reference state enters the DUT. The original FDC receives its public reset and CPU receives RES at SYNC; other peripherals continue ticking.",
        "limits": "Fixed-duration histories, not arbitrary restart/game histories, physical held-RESET behavior, arbitrary media/formatting or universal equivalence. The fixed-end native capture is labelled cold-prefix; its complete declared clock and dot counts are independently checked here.",
        "universal_game_equivalence": False,
    }
    write_json(directory / "result.json", receipt)
    if not receipt["passed"]:
        raise ValueError("Whole-machine restart histories differ: " + str(directory))
    return receipt


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", choices=("rom", "game"), default="rom")
    args = parser.parse_args()
    qualify(args.profile)
