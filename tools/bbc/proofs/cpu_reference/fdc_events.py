"""Original abort/reset/retry event windows through actual 32/96 MHz parents."""

import json
import re
import shutil
import subprocess
from pathlib import Path

from tools.bbc import bus_binding_proof, cpu_events, cpu_interrupt_proof
from tools.bbc.proofs import current_machine, memory_composition
from tools.bbc.proofs.cpu_reference import callback_order, fdc_epoch, source_events
from tools.bbc.proofs.video_cursor import hashes, replace_once
from tools.validation.common import ROOT, source_root, write_json

DIRECTORY = ROOT / "build/bbc/fdc-source-events"
NAMES = [
    "read-abort",
    "write-abort",
    "id-abort",
    *[f"abort-service-{offset}" for offset in (-1, 0, 1)],
    *[f"abort-reset-{offset}" for offset in (-1, 0, 1)],
    *[f"head-reset-{offset}" for offset in (-1, 0, 1)],
    "track-retry",
]


def fixtures(text, names=None):
    lines, cursor, cases = text.splitlines(), 0, {}
    while cursor < len(lines):
        header = lines[cursor].split()
        if (
            len(header) != 4
            or header[0] != "case"
            or header[1] != str(len(cases))
            or not header[3].isdigit()
        ):
            raise ValueError("Malformed device event fixture")
        end = cursor + 1 + int(header[3])
        if end > len(lines) or end == cursor + 1 or header[2] in cases:
            raise ValueError("Truncated or duplicate device event fixture")
        previous = 16
        for line in lines[cursor + 1 : end]:
            row = line.split()
            if len(row) != 7 or row[1] not in {"P", "R", "W"}:
                raise ValueError("Malformed device event observation")
            cycle, address, data, status, nmi, edges = map(int, [row[0], *row[2:]])
            if (
                cycle <= previous
                or not 0 <= address < 8
                or not 0 <= data < 256
                or not 0 <= status < 256
                or nmi not in (0, 1)
                or edges < 0
            ):
                raise ValueError("Invalid device event observation")
            previous = cycle
        cases[header[2]] = "\n".join(lines[cursor:end]) + "\n"
        cursor = end
    if list(cases) != (NAMES if names is None else names):
        raise ValueError("Incomplete original device event inventory")
    return cases


def negative_result(trial, case):
    match = re.fullmatch(
        r"FDC_EPOCH_MISMATCH "
        + re.escape(case)
        + r" cycle ([0-9]+) return ([0-9]+)/([0-9]+) nmi ([01])/([01]) edges ([0-9]+)/([0-9]+)\n",
        trial.stdout,
    )
    if trial.returncode != 1 or trial.stderr or match is None:
        return False
    _, actual_data, expected_data, actual_nmi, expected_nmi, actual_edges, expected_edges = map(
        int, match.groups()
    )
    if case == "read-abort":
        return (
            actual_data == expected_data == 28
            and actual_nmi == expected_nmi == 1
            and actual_edges == 1
            and expected_edges == 2
        )
    return (actual_data, actual_nmi, actual_edges) != (expected_data, expected_nmi, expected_edges)


def retry_fault(source):
    target = "nextRetryCount == 8'h03"
    if source.count(target) != 2:
        raise ValueError("Changed dual ID CRC retry boundary")
    return source.replace(target, "nextRetryCount == 8'h02")


def qualify():
    current_machine.audit()
    premise = source_events.DIRECTORY / "result.json"
    memory_composition.sealed(premise)
    clocks = [current_machine.DIRECTORY / f"clock{m}/result.json" for m in (1, 3)]
    for (_, parent, _), clock in zip(current_machine.parents(), clocks, strict=True):
        receipt = memory_composition.sealed(clock)
        if (
            receipt["inputs"].get(str(parent.relative_to(ROOT)))
            != hashes([parent])[str(parent.relative_to(ROOT))]
        ):
            raise ValueError("Device events use a different parent clock receipt")
    DIRECTORY.mkdir(parents=True, exist_ok=True)
    reference = source_root("jsbeeb")
    helper = Path(__file__).with_suffix(".mjs")
    driver = ROOT / "sim/native/bbc_fdc_branch_test.cpp"
    files = [
        Path(__file__),
        helper,
        driver,
        ROOT / "sim/native/bbc_disc_payload.h",
        ROOT / "sim/native/bbc_fm_media.h",
        ROOT / "tools/bbc/reference_drive.mjs",
        ROOT / "rtl/bbc/Intel8271.scala",
        ROOT / "rtl/bbc/BbcCpu.v",
        ROOT / "build/bbc/ArletCpuEnabled.v",
        *cpu_events.files(),
        ROOT / "build/bbc/disc.ssd",
        premise,
        *clocks,
        current_machine.DIRECTORY / "generation.json",
        *[path for _, path, _ in current_machine.parents()],
        *[blob for _, path, _ in current_machine.parents() for blob in path.parent.glob("*.bin")],
        *sorted((reference / "src").rglob("*.js")),
        *[
            Path(module.__file__)
            for module in (
                current_machine,
                memory_composition,
                callback_order,
                fdc_epoch,
                source_events,
                bus_binding_proof,
                cpu_interrupt_proof,
            )
        ],
        ROOT / "tools/bbc/proofs/video_cursor.py",
        ROOT / "tools/validation/common.py",
        *[Path(shutil.which(tool)) for tool in ("node", "verilator", "c++")],
    ]
    before = hashes(files)
    subprocess.run(
        [
            "node",
            str(helper),
            str(reference),
            str(ROOT / "tools/bbc/reference_drive.mjs"),
            str(ROOT / "build/bbc/disc.ssd"),
            str(DIRECTORY),
        ],
        check=True,
        timeout=60,
    )
    original = json.loads((DIRECTORY / "original.json").read_text())
    table = DIRECTORY / "original.tsv"
    selected = fixtures(table.read_text())
    if original.get("passed") is not True or [case["name"] for case in original["cases"]] != NAMES:
        raise ValueError("Original event execution inventory differs")
    observations = sum(case["rows"] for case in original["cases"])
    artifacts = [table, DIRECTORY / "original.json"]
    for name in ("read-abort", "head-reset--1", "track-retry"):
        path = DIRECTORY / (name + ".tsv")
        path.write_text(re.sub(r"\Acase [0-9]+ ", "case 0 ", selected[name]))
        artifacts.append(path)
    checks = []
    for multiplier, parent, top in current_machine.parents():
        source = parent.read_text()
        variants = [
            ("positive", source, table, 13),
            (
                "lost-abort-retrigger",
                replace_once(
                    source,
                    "assign io_nmiEvent = (abortNmi && (! io_warmReset));",
                    "assign io_nmiEvent = 1'b0;",
                ),
                DIRECTORY / "read-abort.tsv",
                1,
            ),
            (
                "uncancelled-reset",
                replace_once(
                    source,
                    re.findall(r"  assign hostReset = [^;]+;", source)[0],
                    "  assign hostReset = 1'b0;",
                ),
                DIRECTORY / "head-reset--1.tsv",
                1,
            ),
            (
                "missing-retry",
                retry_fault(source),
                DIRECTORY / "track-retry.tsv",
                1,
            ),
        ]
        for name, body, fixture, count in variants:
            case = DIRECTORY / f"{name}{multiplier}"
            case.mkdir(exist_ok=True)
            model = case / "actual.v"
            model.write_text(
                re.sub(
                    r'\$readmemb\("([^\"]+)"',
                    lambda match: '$readmemb("' + str(parent.parent / match[1]) + '"',
                    fdc_epoch.native_model(body, top),
                )
            )
            binary = callback_order.compile_native(
                model,
                case,
                "DeviceEpochProbe",
                driver,
                [ROOT / "sim/native/bbc_disc_payload.h", ROOT / "sim/native/bbc_fm_media.h"],
            )
            trial = subprocess.run(
                [
                    str(binary),
                    str(fixture),
                    str(ROOT / "build/bbc/disc.ssd"),
                    str(multiplier),
                    str(count),
                ],
                capture_output=True,
                text=True,
                timeout=600,
            )
            log = case / "run.log"
            log.write_text(trial.stdout + trial.stderr)
            accepted = (
                trial.returncode == 0
                and trial.stdout
                == f"FDC epoch: 13 cold cases; {observations} observations; 0 differences\n"
                and not trial.stderr
                if name == "positive"
                else negative_result(
                    trial,
                    {
                        "lost-abort-retrigger": "read-abort",
                        "uncancelled-reset": "head-reset--1",
                        "missing-retry": "track-retry",
                    }[name],
                )
            )
            checks.append({"clock_multiplier": multiplier, "name": name, "accepted": accepted})
            artifacts.extend([model, binary, log, case / "build.json", case / "build.log"])
            print(
                f"Original device events {multiplier} {name}: " + ("PASS" if accepted else "FAIL"),
                flush=True,
            )
            if not accepted:
                raise ValueError("Original device events differ; inspect " + str(log))
    current_machine.audit()
    for path in [premise, *clocks]:
        memory_composition.sealed(path)
    stable = before == hashes(files)
    write_json(
        DIRECTORY / "result.json",
        {
            "passed": stable,
            "inputs_unchanged": stable,
            "inputs": before,
            "artifacts": hashes(artifacts),
            "checks": checks,
            "source_cases": 13,
            "observations_per_clock": observations,
            "scope": "Thirteen original public cold command histories through actual parent clocks, decoder, strobes and FDC at 32/96 MHz, with every physical CPU NMI pin sample and explicit retrigger input counted. Ready READ/WRITE/READ ID late-DMA aborts, service and reset before/on/after the abort deadline, cold head-load cancellation and replacement timers, and a logical/physical track mismatch with two seek retries are compared at source checkpoints. No internal DUT state is assigned. Original source setter transitions supply cumulative rising-event counts, including intra-callback retriggers. Missing retrigger and uncancelled reset controls must fail behaviorally.",
            "limits": "Finite declared SSD/clocked-FM histories with held public requests, shared-reset origin and no CPU instruction execution in the held detector probe. Within-callback abort rises use the explicit CPU event input while the retained pin remains high; identical zero-duration sub-slot pin pulses are not claimed. A separate initialized unbounded collector theorem covers all abort phases, including CPU acceptance, under matching callback levels and abort causes. Arbitrary reset/retry/media histories, full executing CPU/game and physical-board equivalence remain open.",
            "universal_game_equivalence": False,
        },
    )
    if not stable:
        raise ValueError("Device event inputs changed")


if __name__ == "__main__":
    qualify()
