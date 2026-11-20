"""Compare cold loading video with independent Hitachi and SAA5050 models."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import re
import subprocess
import tempfile
from pathlib import Path

from tools.validation.common import ROOT, digest, source_root, write_json
from tools.validation.setup import verify

from . import boot_cache, build, crtc_registers, cycle_reference, video96
from .capture import run


def machine_binding():
    """Require the tested component to be the actual machine's video logic.

    Elaborated top names and ROM filenames differ between the two wrappers.
    Only those names are normalized; ROM contents and every other byte remain.
    """
    directory = video96.DIRECTORY
    machine = (directory / "BbcMachine96.v").read_text()
    component = (directory / "BbcVideo96.v").read_text()

    def module(text, name):
        match = re.search(r"^module\s+" + name + r"\b[\s\S]*?^endmodule\b", text, re.M)
        if match is None:
            raise ValueError("Missing generated video module: " + name)
        body = re.sub(r"^module\s+" + name + r"\b", "module BoundVideo", match[0])
        return re.sub(
            r'\$readmemb\("([^\"]+)"',
            lambda rom: '$readmemb("' + digest(directory / rom[1]) + '"',
            body,
        )

    result = {}
    for name in (
        "BbcVideo",
        "Crtc6845",
        "Saa5050Cell",
        "TeletextFont",
        "TeletextAttributes",
        "TeletextGlyphRow",
        "BbcVideoAddress",
    ):
        actual = module(machine, name)
        if actual != module(component, "BbcVideo96" if name == "BbcVideo" else name):
            raise ValueError("Whole-machine video differs from tested module: " + name)
        result[name] = hashlib.sha256(actual.encode()).hexdigest()
    return result


def capture_endpoint(cycles, summary, sync):
    """Saved full boots run to their own next complete field; prefixes do not."""
    endpoint = summary["cycles"]
    if type(endpoint) is not int or endpoint != sync["final_cycle"]:
        raise ValueError("Original loading capture endpoints disagree")
    requested = cycles // 16
    if cycles == boot_cache.BOOT_CYCLES:
        # MODE 4 Elite-A fields have 39,936 CPU slots. The boot runner saves
        # only after its next original field, never at an invented endpoint.
        if not requested < endpoint <= requested + 40000:
            raise ValueError("Own boot did not end within its next complete field")
        if not sync["transitions"] or sync["transitions"][-1] != {
            "cycle": endpoint,
            "level": True,
            "control": 4,
        }:
            raise ValueError("Own boot endpoint is not the original MODE 4 VSYNC rise")
    elif endpoint != requested:
        raise ValueError("Cold prefix advanced beyond its requested duration")
    return endpoint


def original_capture(receipt, cycles, keys, observers):
    """Reuse original observations only; a prior DUT result is not inherited."""
    report = json.loads(receipt.read_text())
    reference = ROOT / report.get(
        "reference_directory", str((receipt.parent / "reference").relative_to(ROOT))
    )
    configuration = receipt.parent / "reference.json"
    saved = json.loads(configuration.read_text())
    if (
        report.get("checks", {}).get("unchanged_inputs") is not True
        or report.get("requested_cycles") != cycles
        or report["inputs"]["reference"] != boot_cache.inputs("reference")
        or saved.get("cycles") != cycles
        or saved.get("load_state")
        or saved.get("scenario")
        or any(saved.get(name) != value for name, value in boot_cache.REFERENCE_OPTIONS.items())
        or Path(saved["keys"]).read_bytes() != keys.read_bytes()
    ):
        raise ValueError("Original loading observations have stale or different inputs")
    files = report["inputs"]["files"]
    sources = [configuration, Path(saved["keys"]), *observers[:4]]
    if any(files.get(str(file.relative_to(ROOT))) != digest(file) for file in sources):
        raise ValueError("Original loading producer changed")
    prefix = str(reference.relative_to(ROOT)) + "/"
    expected = {name: sha for name, sha in report["artifacts"].items() if name.startswith(prefix)}
    if not expected or expected != build.hashes(reference.glob("*")):
        raise ValueError("Original loading observation bytes changed")
    endpoint = capture_endpoint(
        cycles,
        json.loads((reference / "summary.json").read_text()),
        json.loads((reference / "raw-vsync.json").read_text()),
    )
    metadata = json.loads((reference / "video-pins.json").read_text())
    if metadata["count"] != endpoint - 16 or metadata["uncompressed_bytes"] != (endpoint - 16) * 20:
        raise ValueError("Incomplete original loading observations")
    return reference, {
        "receipt": str(receipt.relative_to(ROOT)),
        "receipt_sha256": digest(receipt),
        "sources": build.hashes(sources),
        "outputs": expected,
        "scope": "Reuse of unchanged original CPU observations only; all new DUT comparisons execute afresh, regardless of the earlier DUT result.",
    }


def bitmap_disagreement(javascript, chip):
    """Retain disagreements between unchanged models without filtering DUT dots."""
    offset = differences = 0
    details = []
    with gzip.open(javascript, "rb") as original, gzip.open(chip, "rb") as independent:
        while chunk := original.read(1 << 20):
            expected = independent.read(len(chunk))
            if len(expected) != len(chunk):
                raise ValueError("Original bitmap observation lengths differ")
            if chunk != expected:
                for index, (js, c) in enumerate(zip(chunk, expected)):
                    if js != c:
                        differences += 1
                        if len(details) < 1024:
                            dot = offset + index
                            slot = 16 + dot // 8
                            details.append(
                                {
                                    "cpu_slot": slot,
                                    "physical_clock": slot * 48 + 5 + dot % 8 * 6,
                                    "dot": dot % 8,
                                    "javascript": js,
                                    "original_c": c,
                                }
                            )
            offset += len(chunk)
        if independent.read(1):
            raise ValueError("Original bitmap observation lengths differ")
    return {
        "qualified": False,
        "different_dots": differences,
        "all_dots": offset,
        "differences": details,
        "scope": "Original JavaScript versus original C bitmap renderer; every dot remains in the DUT comparison. Details are limited to the first 1024 disagreements; the total count is complete.",
    }


def component_run(command, cwd, log, timeout):
    # These bounded video-component checks do not consume whole-machine
    # capture leases or wait behind multi-minute gameplay captures.
    with log.open("w") as stream:
        subprocess.run(
            command, cwd=cwd, stdout=stream, stderr=subprocess.STDOUT, check=True, timeout=timeout
        )


def qualify(cycles=32000000, reference_receipt=None):
    if type(cycles) is not int or cycles <= 256 or cycles % 32:
        raise ValueError("Duration must contain whole 1 MHz character slots after reset")
    verify()
    if not cycle_reference.current():
        raise ValueError("Build the independent cycle simulator first")
    source = crtc_registers.independent_sources()
    directory = Path(tempfile.mkdtemp(prefix="loading-video-", dir=build.BBC))
    reference = directory / "reference"
    keys = directory / "keys"
    keys.write_text(
        "".join(
            line + "\n"
            for line in (build.BBC / "boot.keys").read_text().splitlines()
            if int(line.split()[0]) < cycles
        )
    )
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
            "output": str(reference),
            **(
                {"save_state": str(directory / "reference-own.state")}
                if cycles == boot_cache.BOOT_CYCLES
                else {}
            ),
        },
    )
    cpp = ROOT / "sim/native/bbc_loading_video_test.cpp"
    driver = ROOT / "sim/native/bbc_loading_teletext_oracle.c"
    bitmap_driver = ROOT / "sim/native/bbc_loading_bitmap_oracle.c"
    observers = [
        ROOT / ("tools/bbc/" + name)
        for name in (
            "reference_boot_video_trace.mjs",
            "reference_boot_video_pins.mjs",
            "reference_boot_sync.mjs",
            "reference_capture.mjs",
            "reference_loading_bitmap.mjs",
        )
    ]
    dependencies = [
        Path(__file__),
        ROOT / "tools/bbc/crtc_registers.py",
        driver,
        bitmap_driver,
        *observers,
        *[source / name for name in crtc_registers.PIN],
    ]
    # Oracle and Python changes are guarded separately and need no RTL rebuild.
    binary, native_build = video96.compile("loading", cpp, [], link_flags=("-lz",))
    oracle = directory / "oracle"
    with (directory / "oracle-build.log").open("w") as log:
        subprocess.run(
            [
                "cc",
                "-O2",
                "-ffunction-sections",
                "-fdata-sections",
                "-DBEEBJIT_HEADLESS",
                "-I" + str(source),
                str(driver),
                *[str(source / name) for name in ("util.c", "util_string.c", "log.c")],
                "-Wl,--gc-sections",
                "-lm",
                "-lpthread",
                "-lz",
                "-o",
                str(oracle),
            ],
            stdout=log,
            stderr=subprocess.STDOUT,
            check=True,
            timeout=120,
        )
    bitmap_oracle = directory / "bitmap-oracle"
    component_run(
        [
            "cc",
            "-O2",
            "-ffunction-sections",
            "-fdata-sections",
            "-DBEEBJIT_HEADLESS",
            "-I" + str(source),
            str(bitmap_driver),
            *[
                str(source / name)
                for name in ("render.c", "teletext.c", "util.c", "util_string.c", "log.c")
            ],
            "-Wl,--gc-sections",
            "-lm",
            "-lpthread",
            "-lz",
            "-o",
            str(bitmap_oracle),
        ],
        ROOT,
        directory / "bitmap-oracle-build.log",
        120,
    )

    def inputs():
        return {
            "reference": boot_cache.inputs("reference"),
            "generation": video96.snapshot(),
            "machine_binding": machine_binding(),
            "files": build.hashes(
                [
                    *dependencies,
                    cpp,
                    configuration,
                    keys,
                    build.BBC / "disc.ssd",
                    binary,
                    oracle,
                    bitmap_oracle,
                ]
            ),
            "native_build": native_build,
        }

    before = inputs()
    print(f"Independent loading video, {cycles} common clocks: {directory}", flush=True)
    if reference_receipt is not None:
        reference_receipt = Path(reference_receipt).resolve()
        reference, producer = original_capture(reference_receipt, cycles, keys, observers)
        before["reused_original"] = producer
        print(f"Reusing unchanged original CPU observations: {reference}", flush=True)
    else:
        run(
            [
                "node",
                "--import",
                str(observers[0]),
                "--import",
                str(observers[1]),
                "--import",
                str(observers[2]),
                str(ROOT / "tools/bbc/reference.mjs"),
                str(configuration),
            ],
            ROOT,
            directory / "reference.log",
            timeout=boot_cache.BOOT_TIMEOUT_SECONDS,
        )
    pins = reference / "video-pins.bin.gz"
    cells = directory / "teletext-cells.bin.gz"
    component_run([str(oracle), str(pins), str(cells)], ROOT, directory / "oracle.log", 300)
    ram = directory / "dut-own.ram"
    trace = reference / "video-transactions.tsv"
    javascript_bitmap = directory / "javascript-bitmap-pins.bin.gz"
    component_run(
        [
            "node",
            str(observers[4]),
            str(source_root("jsbeeb")),
            str(pins),
            str(trace),
            str(javascript_bitmap),
        ],
        ROOT,
        directory / "javascript-bitmap.log",
        boot_cache.BOOT_TIMEOUT_SECONDS,
    )
    bitmap = directory / "bitmap-pins.bin.gz"
    component_run(
        [str(bitmap_oracle), str(pins), str(trace), str(bitmap)],
        ROOT,
        directory / "bitmap.log",
        300,
    )
    disagreement = bitmap_disagreement(javascript_bitmap, bitmap)
    write_json(directory / "original-bitmap-disagreement.json", disagreement)
    fixtures = [
        pins,
        trace,
        cells,
        bitmap,
        javascript_bitmap,
        directory / "original-bitmap-disagreement.json",
    ]
    original_fixtures = build.hashes(fixtures)
    component_run(
        [str(binary), str(pins), str(cells), str(trace), str(ram), str(bitmap)],
        video96.DIRECTORY,
        directory / "dut.log",
        boot_cache.BOOT_TIMEOUT_SECONDS * 3,
    )
    original = json.loads((reference / "video-pins.json").read_text())
    transactions = json.loads((reference / "video-transactions.json").read_text())
    chip = json.loads((directory / "oracle.log").read_text().splitlines()[-1])
    bitmap_reference = json.loads((directory / "bitmap.log").read_text().splitlines()[-1])
    javascript_reference = json.loads(
        (directory / "javascript-bitmap.log").read_text().splitlines()[-1]
    )
    dut = json.loads((directory / "dut.log").read_text().splitlines()[-1])
    transaction_count = ram_count = 0
    with trace.open() as stream:
        for line in stream:
            transaction_count += 1
            ram_count += line.split()[1] == "ram"
    endpoint = capture_endpoint(
        cycles,
        json.loads((reference / "summary.json").read_text()),
        json.loads((reference / "raw-vsync.json").read_text()),
    )
    slots = endpoint - 16
    after = inputs()
    if reference_receipt is not None:
        _, after["reused_original"] = original_capture(reference_receipt, cycles, keys, observers)
    checks = {
        "complete": original["count"] == dut["slots"] == slots
        and original["initial_cycle"] == transactions["initial_cycle"] == 16
        and original["final_cycle"] == transactions["final_cycle"] == endpoint
        and original["record_bytes"] == 20
        and original["uncompressed_bytes"] == slots * 20
        and transaction_count == transactions["transactions"]
        and dut["ram_writes"] == ram_count
        and chip["character_samples"] == dut["character_samples"] == (slots + 1) // 2
        and dut["sync_observations"] == slots * 49
        and dut["fetches"] == original["video_fetches"]
        and dut["teletext_dots"] == original["teletext_slots"] * 6 > 0
        and bitmap_reference["slots"] == slots
        and bitmap_reference["transactions"] == transaction_count
        and bitmap_reference["uncompressed_bytes"] == slots * 8
        and bitmap_reference["bitmap_slots"] + original["teletext_slots"] == slots
        and bitmap_reference["bitmap_dots"] == dut["bitmap_dots"] > 0,
        "original_bitmap_counts": all(
            javascript_reference.get(key) == value for key, value in bitmap_reference.items()
        )
        and disagreement["all_dots"] == slots * 8,
        "bitmap_rgb": dut["bitmap_differences"] == 0,
        "teletext_rgb": dut["rgb_differences"] == 0,
        "teletext_coordinates": dut["coordinate_differences"] == 0,
        "all_hsync_vsync": dut["sync_differences"] == 0,
        "all_video_fetches": dut["fetch_differences"] == 0,
        "own_final_ram": ram.read_bytes() == (reference / "video-final.ram").read_bytes(),
        "original_bitmap_own_ram": Path(str(javascript_bitmap) + ".ram").read_bytes()
        == (reference / "video-final.ram").read_bytes(),
        "declared_initial_ram": (reference / "video-initial.ram").read_bytes()
        == bytes(256) + bytes([255]) * (32768 - 256),
        "supported_profile": dut["unsupported_edges"] == 0,
        "unchanged_inputs": before == after,
        "unchanged_comparison_fixtures": original_fixtures == build.hashes(fixtures),
    }
    report = {
        "passed": all(checks.values()),
        "checks": checks,
        "dut": dut,
        "original": original,
        "original_chip": chip,
        "original_bitmap": bitmap_reference,
        "original_javascript_bitmap": javascript_reference,
        "original_bitmap_disagreement": disagreement,
        "comparison_fixtures": original_fixtures,
        "reference_directory": str(reference.relative_to(ROOT)),
        "inputs": before,
        "requested_cycles": cycles,
        "artifacts": build.hashes(
            [
                *reference.glob("*"),
                cells,
                bitmap,
                javascript_bitmap,
                Path(str(javascript_bitmap) + ".ram"),
                directory / "original-bitmap-disagreement.json",
                ram,
                directory / "dut.log",
                directory / "oracle.log",
                directory / "bitmap.log",
                directory / "javascript-bitmap.log",
            ]
        ),
        "scope": "Cold actual-machine video component with its own power-on RAM. Independent original CPU writes drive the bus; every RAM fetch address/data and every HS/VS level is checked. Original Hitachi timing and cursor control drive unchanged C SAA5050 and bitmap renderers through public APIs. Every teletext and bitmap RGB dot, including borders, is compared. An independent unchanged original JavaScript Video replays the same bus with its own RAM, cross-checking every CRTC, fetch, sync and cursor record against the original CPU capture. Its bitmap disagreements with the independent C renderer are retained separately without omitting any DUT dot.",
        "limits": "Component replay, not a second DUT CPU execution. Teletext dot association uses the declared serializer latency, not a physical chip phase measurement. No claim over arbitrary inputs or physical hardware.",
    }
    write_json(directory / "result.json", report)
    print(f"Loading video: {checks}; {directory}/result.json", flush=True)
    if not report["passed"]:
        raise ValueError("Loading video differs; independent captures retained")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cycles", type=int, default=32000000)
    parser.add_argument(
        "--reference-receipt",
        type=Path,
        help="Reuse only guarded unchanged original CPU observations; execute new DUT comparisons",
    )
    args = parser.parse_args()
    qualify(args.cycles, args.reference_receipt)
