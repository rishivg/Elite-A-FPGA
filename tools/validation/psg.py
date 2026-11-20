"""Independent BBC PSG protocol, transfer, divider and digital PDM checks.

Absolute phase and analog filter response are deliberately separate gaps.
"""

import bisect
import json
import subprocess
from pathlib import Path

from .common import HERE, ROOT, source_root, publish_latest, write_json


def trace(path: Path):
    return [tuple(map(int, line.split())) for line in path.read_text().splitlines()]


def tone_intervals(items, channel=0):
    edges = []
    previous = 0
    for cycle, value in items:
        bit = (value >> channel) & 1
        if bit != previous:
            edges.append(cycle)
            previous = bit
    intervals = [b - a for a, b in zip(edges, edges[1:]) if a > 1000]
    return sorted(set(intervals))


def capture(case: Path, arguments: list[str], duration: int):
    ref = case / "reference"
    dut = case / "dut"
    ref.mkdir(parents=True)
    dut.mkdir()
    with (case / "capture.log").open("w") as log:
        subprocess.run(
            [
                "node",
                str(HERE / "psg_reference.mjs"),
                str(source_root("jsbeeb")),
                *arguments,
                str(ref),
                str(duration),
            ],
            check=True,
            stdout=log,
            stderr=subprocess.STDOUT,
            timeout=60,
        )
        subprocess.run(
            [
                str(ROOT / "build/rtl/obj_validate_psg/validate_psg"),
                *arguments,
                str(dut),
                str(duration),
            ],
            check=True,
            stdout=log,
            stderr=subprocess.STDOUT,
            timeout=60,
        )
    return ref, dut


def transfer_check(reference: Path, dut: Path, duration: int):
    tables = json.loads((reference / "amplitudes.json").read_text())
    registers = trace(dut / "registers.tsv")
    times = [r[0] for r in registers]
    mismatches = []
    checked = 0
    covered = set()
    for cycle, sample, tones, noise in trace(dut / "samples.tsv"):
        if (
            cycle != checked * 8 + 7
            or not 0 <= sample <= 252
            or not 0 <= tones <= 7
            or noise not in (0, 1)
        ):
            raise ValueError("Invalid or out-of-order PSG sample")
        row = registers[bisect.bisect_right(times, cycle) - 1]
        volumes = row[5:9]
        expected = sum(tables[ch][volumes[ch]] for ch in range(3) if tones & (1 << ch)) + (
            tables[3][volumes[3]] if noise else 0
        )
        checked += 1
        for ch in range(4):
            high = bool(tones & (1 << ch)) if ch < 3 else bool(noise)
            if high:
                covered.add((ch, volumes[ch]))
        if sample != expected and len(mismatches) < 16:
            mismatches.append(
                {
                    "cycle": cycle,
                    "expected": expected,
                    "actual": sample,
                    "attenuation": list(volumes),
                }
            )
    return {
        "passed": checked == duration // 8 and checked > 0 and not mismatches,
        "checked_samples": checked,
        "expected_samples": duration // 8,
        "first_mismatches": mismatches,
        "covered_channel_attenuation": sorted(covered),
        "reference_amplitudes": tables,
    }


def pdm_check(path: Path, duration: int):
    result = json.loads(path.read_text())
    error = 256 * result["pdm_ones"] - result["sample_sum"]
    result["passed"] = bool(
        result["passed"]
        and result["cycles"] == duration
        and 0 <= result["pdm_ones"] <= duration
        and 0 <= result["sample_sum"] <= 252 * duration
        and -255 <= error <= 0
        and -255 <= result["min_integral_error"] <= error <= result["max_integral_error"] <= 0
    )
    return result


def protocol_probe(directory: Path):
    # Every latch byte and every 7-bit continuation value for every latch
    # selector/low nibble. Long holds additionally exercise all audible levels.
    values = [0x81, 0, 0xA1, 0, 0xC1, 0, 0x9F, 0xBF, 0xDF, 0xE0, 0xFF]
    preamble = len(values)
    for selector in range(8):
        for low in range(16):
            values.append(0x80 | (selector << 4) | low)
            values.extend(range(128))
    events = [(i * 16, value) for i, value in enumerate(values)]
    cycle = len(events) * 16
    # Sound each channel alone, and keep each attenuation long enough for
    # periodic noise's high bit as well as the square-wave channels.
    for channel in range(4):
        for other in range(4):
            events.append((cycle, 0x9F | (other << 5)))
            cycle += 16
        events.append((cycle, 0xE0))
        cycle += 16
        for volume in range(16):
            events.append((cycle, 0x90 | (channel << 5) | volume))
            cycle += 20000
    case = directory / "protocol"
    case.mkdir(parents=True)
    eventfile = case / "events.tsv"
    eventfile.write_text("".join(f"{c}\t{v}\n" for c, v in events))
    ref, dut = capture(case, ["--script", str(eventfile)], cycle + 10000)
    expected = trace(ref / "registers.tsv")[preamble:]
    actual = trace(dut / "registers.tsv")[preamble:]
    mismatches = [
        {"write": i + preamble, "reference": r, "dut": d}
        for i, (r, d) in enumerate(zip(expected, actual))
        if r != d
    ][:16]
    transfer = transfer_check(ref, dut, cycle + 10000)
    pdm = pdm_check(dut / "pdm.json", cycle + 10000)
    covered = {tuple(x) for x in transfer["covered_channel_attenuation"]}
    audible = {(ch, vol) for ch in range(4) for vol in range(15)}
    result = {
        "registers_passed": bool(expected) and len(expected) == len(actual) and not mismatches,
        "checked_writes": len(expected),
        "first_mismatches": mismatches,
        "transfer": transfer,
        "all_audible_attenuations_covered": audible <= covered,
        "pdm": pdm,
        "reset_alignment": "Identical serial register initialization; only initialization snapshots excluded",
    }
    result["passed"] = (
        result["registers_passed"]
        and transfer["passed"]
        and result["all_audible_attenuations_covered"]
        and pdm["passed"]
    )
    write_json(case / "result.json", result)
    return result


def run_probes(directory: Path) -> dict:
    results = []
    for period, control in [
        (0, 0),
        (1, 0),
        (6, 0),
        (165, 0),
        (1023, 0),
        (6, 4),
        (6, 1),
        (6, 2),
        (1, 3),
        (6, 3),
        (165, 7),
        (1023, 7),
        (0, 3),
        (0, 7),
    ]:
        case = directory / f"p{period}-n{control}"
        ref = case / "reference"
        dut = case / "dut"
        ref, dut = capture(case, [str(period), str(control)], 1200000)
        expected = tone_intervals(trace(ref / "tones.tsv"))
        actual = tone_intervals(trace(dut / "tones.tsv"))
        rnoise = [(c, v) for c, v in trace(ref / "noise.tsv") if c > 144]
        dnoise = [(c, v) for c, v in trace(dut / "noise.tsv") if c > 144]
        # Retain the first 32 shifts after the controlled register write. The
        # HDL write itself changes seed; exclude that reset event explicitly.
        dsequence = [v for c, v in dnoise][:32]
        rsequence = [v for c, v in rnoise][:32]
        rinterval = sorted({b[0] - a[0] for a, b in zip(rnoise, rnoise[1:]) if a[0] > 1000})
        dinterval = sorted({b[0] - a[0] for a, b in zip(dnoise, dnoise[1:]) if a[0] > 1000})
        channels = [
            {
                "channel": ch,
                "reference": tone_intervals(trace(ref / "tones.tsv"), ch),
                "dut": tone_intervals(trace(dut / "tones.tsv"), ch),
            }
            for ch in range(3)
        ]
        result = {
            "period": period,
            "noise_control": control,
            "reference_tone_half_period_cycles": expected,
            "dut_tone_half_period_cycles": actual,
            "tone_period_passed": bool(expected) and expected == actual,
            "reference_lfsr_sequence": rsequence,
            "dut_lfsr_sequence": dsequence,
            "lfsr_sequence_passed": bool(rsequence) and rsequence == dsequence,
            "reference_noise_shift_intervals": rinterval,
            "dut_noise_shift_intervals": dinterval,
            "noise_rate_passed": bool(rinterval) and rinterval == dinterval,
            "channels": channels,
            "all_channels_passed": all(
                c["reference"] and c["reference"] == c["dut"] for c in channels
            ),
            "pdm": pdm_check(dut / "pdm.json", 1200000),
            "phase_equivalence": "unvalidated: initialization and bus-phase correspondence require further checks",
        }
        if period == 0 and control & 3 == 3:
            result["reference_disagreement"] = (
                "jsbeeb uses raw tone-2 zero as a zero-length noise divider; RTL uses the SN76489 zero-as-1024 convention. Resolve against hardware before certifying this corner."
            )
        result["passed"] = (
            result["tone_period_passed"]
            and result["lfsr_sequence_passed"]
            and result["noise_rate_passed"]
            and result["all_channels_passed"]
            and result["pdm"]["passed"]
        )
        write_json(case / "result.json", result)
        results.append(result)
    protocol = protocol_probe(directory)
    result = {
        "passed": all(r["passed"] for r in results) and protocol["passed"],
        "cases": results,
        "protocol": protocol,
        "coverage_gaps": [
            "absolute phase and transient divider writes",
            "tone-2 zero-period reference/hardware disagreement",
            "analog DAC/filter response",
        ],
    }
    write_json(directory / "result.json", result)
    return result


if __name__ == "__main__":
    import tempfile

    from .common import CACHE, LOCK, ROOT, digest
    from .setup import prepare

    prepare()
    destination = Path(tempfile.mkdtemp(prefix="psg-", dir=CACHE))
    write_json(
        destination / "manifest.json",
        {
            "reference": LOCK,
            "input_hashes": {
                str(p.relative_to(ROOT)): digest(p)
                for p in [
                    HERE / "psg.py",
                    HERE / "psg_reference.mjs",
                    ROOT / "rtl/audio/Psg.scala",
                    ROOT / "sim/native/validate_psg.cpp",
                    ROOT / "build/rtl/Psg.v",
                    ROOT / "build/rtl/obj_validate_psg/validate_psg",
                ]
            },
        },
    )
    outcome = run_probes(destination)
    publish_latest(CACHE / "psg-latest", destination)
    print(f"PSG: {'PASS' if outcome['passed'] else 'FAIL'}; report: {destination}/result.json")
    raise SystemExit(0 if outcome["passed"] else 1)
