"""Compare real controller boundaries and raw writes at both physical clocks."""

import json
import re
import subprocess
from pathlib import Path

from tools.bbc import build, bus_binding_proof, disc_media, probe, video96
from tools.validation.common import ROOT, digest, source_root, write_json

DIRECTORY = build.BBC / "disc-clocked-boundaries"
DRIVER = ROOT / "sim/native/bbc_disc_batch_test.cpp"
HEADERS = [
    ROOT / "sim/native" / name
    for name in ("bbc_own_prefix.h", "bbc_fm_media.h", "bbc_disc_payload.h")
]


def clock_support(path, multiplier):
    """Require the actual parent's enabled-edge schedule, not just its port name."""
    report = json.loads(path.read_text())
    if (
        report.get("passed") is not True
        or report.get("inputs_unchanged") is not True
        or report.get("clock_multiplier") != multiplier
        or report.get("negative_control_detected") is not True
    ):
        raise ValueError("Incomplete actual parent clock proof")
    for category in ("inputs", "artifacts"):
        files = report.get(category, {})
        if not files or any(digest(ROOT / name) != sha for name, sha in files.items()):
            raise ValueError("Changed actual parent clock evidence")
    return report


def driver96(text, parent, module):
    """Reproduce the actual parent enable and physical storage-write phase."""
    ports = bus_binding_proof.ports(parent, "Intel8271", "intel8271_1")
    if ports.get("clk", "").strip() != "clk" or ports.get("reset", "").strip() != "reset":
        raise ValueError("Changed controller clock/reset binding")
    extra = re.findall(r"input\s+wire\s+(_zz_\w+)\s*[,)]", module)
    if len(extra) != 1 or extra[0] not in ports:
        raise ValueError("Expected exactly one generated controller enable input")
    connection = re.sub(r"\s+", "", ports[extra[0]])
    if (
        len(
            re.findall(
                r"assign\s+" + re.escape(connection) + r"\s*=\s*\(1'b1 && clock32\);", parent
            )
        )
        != 1
    ):
        raise ValueError("Changed actual controller clock-enable binding")
    if not re.search(
        r"assign\s+io_storageWrite\s*=\s*\(intel8271_1_io_storageWrite && clock32\);", parent
    ):
        raise ValueError("Changed actual physical storage-write gating")
    targets = {
        "v.clk = 0;\n                v.eval();": f"v.{extra[0]} = !v.reset && (clock + 1) % 3 == 2;\n                v.clk = 0;\n                v.eval();",
        "if (!v.reset && v.io_storageWrite)": f"if (!v.reset && v.{extra[0]} && v.io_storageWrite)",
        "target = time * 16,": "target = time * 48 + 2,",
    }
    for correct, replacement in targets.items():
        expected = 2 if correct.startswith("v.clk = 0;") else 1
        if text.count(correct) != expected:
            raise ValueError("Changed maintained batch-driver clock target")
        text = text.replace(correct, replacement, 1)
    if text.count("(clock + 1 + 15) / 16") != 2:
        raise ValueError("Changed raw/payload observation epochs")
    text = text.replace("(clock + 1 + 15) / 16", "((clock + 1 - 2) / 3 + 15) / 16")
    return text, extra[0]


def compile_model(directory, module, driver):
    directory.mkdir(parents=True, exist_ok=True)
    rtl, cpp = directory / "devices.v", directory / "driver.cpp"
    rtl.write_text("`timescale 1ns/1ps\n" + module + "\n")
    for header in HEADERS:
        driver = driver.replace('"' + header.name + '"', '"' + str(header) + '"')
    cpp.write_text(driver)
    with (directory / "build.log").open("w") as log:
        subprocess.run(
            [
                "verilator",
                "--cc",
                "--exe",
                "--build",
                "-j",
                "1",
                "--savable",
                "-Wno-fatal",
                "--top-module",
                "Intel8271",
                "-CFLAGS",
                "-O3 -std=c++17 -flto -Werror=return-type",
                "-LDFLAGS",
                "-flto",
                "-MAKEFLAGS",
                "-B OPT_FAST=-O3",
                "--Mdir",
                str(directory),
                str(rtl),
                str(cpp),
            ],
            cwd=ROOT,
            stdout=log,
            stderr=subprocess.STDOUT,
            check=True,
            timeout=180,
        )
    return directory / "VIntel8271"


def replay(binary, cases, directory, cwd, cache):
    directory.mkdir(parents=True, exist_ok=True)
    manifest = directory / "manifest.tsv"
    manifest.write_text("".join(f"{path} {case['name']}\n" for case, path in cases))
    for case, _ in cases:
        for suffix in (".log", ".writes.tsv", ".raw.tsv"):
            (directory / (case["name"] + suffix)).unlink(missing_ok=True)
    run = subprocess.run(
        [str(binary), str(manifest), str(build.BBC / "disc.ssd"), str(directory), str(int(cache))],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=1800,
    )
    (directory / "batch.log").write_text(run.stdout + run.stderr)
    results = []
    for case, _ in cases:
        log = directory / (case["name"] + ".log")
        raw = directory / (case["name"] + ".raw.tsv")
        payload = directory / (case["name"] + ".writes.tsv")
        results.append(
            {
                "name": case["name"],
                "rows": case["rows"],
                "bus_passed": run.returncode == 0
                and not run.stderr
                and log.is_file()
                and log.read_text() == f"{case['rows']} original bus observations; 0 differences\n",
                "raw_passed": raw.is_file()
                and [list(map(int, line.split())) for line in raw.read_text().splitlines()]
                == disc_media.raw_writes(case["rawFlux"]),
                "payload_passed": payload.is_file()
                and [list(map(int, line.split())) for line in payload.read_text().splitlines()]
                == case["storageWrites"],
            }
        )
    return {
        "results": results,
        "execution": run.stdout,
        "stderr": run.stderr,
        "returncode": run.returncode,
    }


def passed(report):
    return bool(report["results"]) and all(
        all(case[key] for key in ("bus_passed", "raw_passed", "payload_passed"))
        for case in report["results"]
    )


def qualify():
    if not build.generation_current():
        raise ValueError("Generate the current production machine before checking disc timing")
    video96.generate()
    DIRECTORY.mkdir(parents=True, exist_ok=True)
    clocks = [build.BBC / f"machine-clock-proof{m}/result.json" for m in (1, 3)]
    evidence = [clock_support(path, m) for path, m in zip(clocks, (1, 3), strict=True)]
    script = Path(__file__).with_name("reference_disc_boundaries.mjs")
    media_script = ROOT / "tools/bbc/reference_disc_media.mjs"
    files = [
        Path(__file__),
        Path(build.__file__),
        Path(bus_binding_proof.__file__),
        Path(disc_media.__file__),
        Path(probe.__file__),
        Path(video96.__file__),
        *clocks,
        script,
        media_script,
        DRIVER,
        *HEADERS,
        ROOT / "tools/bbc/reference_disc_media_base.mjs",
        ROOT / "tools/bbc/reference_drive.mjs",
        *[
            source_root("jsbeeb") / "src" / name
            for name in ("intel-fdc.js", "scheduler.js", "disc.js", "fdc.js")
        ],
        build.BBC / "BbcMachine.v",
        video96.DIRECTORY / "BbcMachine96.v",
        build.BBC / "disc.ssd",
        *build.BBC.glob("BbcMachine.v_*.bin"),
        *video96.DIRECTORY.glob("BbcMachine96.v_*.bin"),
    ]
    before = build.hashes(files)
    originals = []
    for name, reference in (("boundary", script), ("media", media_script)):
        output = DIRECTORY / (name + ".json")
        with (DIRECTORY / (name + "-reference.log")).open("w") as log:
            subprocess.run(
                [
                    "node",
                    str(reference),
                    str(source_root("jsbeeb")),
                    str(build.BBC / "disc.ssd"),
                    str(output),
                ],
                cwd=ROOT,
                stdout=log,
                stderr=subprocess.STDOUT,
                check=True,
                timeout=120,
            )
        metadata = json.loads(output.read_text())
        if metadata.get("own_cold_cases") is not True:
            raise ValueError("Reference histories are not independently cold")
        selected = (
            metadata["cases"]
            if name == "boundary"
            else [
                next(c for c in metadata["cases"] if c["name"] == n)
                for n in ("normal", "abort-two", "rewrite-abort")
            ]
        )
        if name == "boundary" and [c.get("delta") for c in selected] != [-1, 0, 1, 127, 128]:
            raise ValueError("Incomplete independent interrupt boundaries")
        for case in selected:
            path = DIRECTORY / case["fixture"]
            if case["rows"] < 100 or not path.is_file():
                raise ValueError("Truncated independent controller history")
            originals.append((case, path))
    original_files = [
        DIRECTORY / "boundary.json",
        DIRECTORY / "media.json",
        *[path for _, path in originals],
    ]
    original_hashes = build.hashes(original_files)
    generated = (video96.DIRECTORY / "BbcMachine96.v").read_text()
    module96 = bus_binding_proof.module(generated, "Intel8271")
    parent96 = bus_binding_proof.module(generated, "BbcMachine96")
    transformed, enable = driver96(DRIVER.read_text(), parent96, module96)
    binary32 = compile_model(
        DIRECTORY / "model32", probe.device_rtl(["Intel8271"]), DRIVER.read_text()
    )
    binary96 = compile_model(DIRECTORY / "model96", module96, transformed)
    reports = []
    for multiplier, binary, cwd in ((1, binary32, build.BBC), (3, binary96, video96.DIRECTORY)):
        report = replay(binary, originals, DIRECTORY / f"positive{multiplier}", cwd, True)
        reports.append({"master_clock_hz": 32000000 * multiplier, **report})
        print(reports[-1], flush=True)
    target = f"if (!v.reset && v.{enable} && v.io_storageWrite)"
    if transformed.count(target) != 1:
        raise ValueError("Missing actual physical WRITE gate mutation")
    bad_binary = compile_model(
        DIRECTORY / "ungated-write",
        module96,
        transformed.replace(target, "if (!v.reset && v.io_storageWrite)"),
    )
    normal = [pair for pair in originals if pair[0]["name"] == "normal"]
    fault = replay(bad_binary, normal, DIRECTORY / "ungated-observations", video96.DIRECTORY, True)
    detected = not passed(fault) and any(not c["raw_passed"] for c in fault["results"])
    stable = (
        before == build.hashes(files)
        and original_hashes == build.hashes(original_files)
        and evidence == [clock_support(path, m) for path, m in zip(clocks, (1, 3), strict=True)]
    )
    result = {
        "passed": stable and all(passed(r) for r in reports) and detected,
        "inputs": before,
        "inputs_unchanged": stable,
        "originals": original_hashes,
        "clock_proofs": evidence,
        "reports": reports,
        "ungated_write_fault": {"detected": detected, **fault},
        "artifacts": build.hashes(
            [
                p
                for p in DIRECTORY.rglob("*")
                if p.is_file() and p.suffix in {".log", ".tsv", ".cpp", ".v"}
            ]
        ),
        "scope": "Eight independently executed cold Original histories at both actual physical clocks. Five first-data/late-DMA boundaries and three normal/partial/rewrite WRITE histories. Every bus observation, payload write and raw FM word/time. Actual generated parent clock-enable and WRITE gate checked; actual missing WRITE gate must fail. Only successful own executed prefixes are restored; no Original state injection.",
        "limits": "Finite directed histories in declared standard-media/digital-reset profile. Not universal peripheral, CPU, whole-game or physical equivalence.",
        "universal_game_equivalence": False,
    }
    write_json(DIRECTORY / "result.json", result)
    if not result["passed"]:
        raise ValueError("Clocked controller histories differ; all outputs retained")
    return result


if __name__ == "__main__":
    qualify()
