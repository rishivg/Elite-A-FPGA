"""Compare every tone period and a complete white-noise cycle with original PSG code."""

from __future__ import annotations

import argparse
import json
import subprocess
import time
from pathlib import Path

from tools.validation.common import ROOT, source_root, write_json
from tools.validation.setup import verify

from . import audio_probe, build


def tone_fixture():
    writes, cycle, previous = [], 0, 1024
    for period in range(1024):
        values = []
        for channel in range(3):
            values.extend([0x80 | (channel << 5) | (period & 15), period >> 4])
        values.extend(0x90 | (channel << 5) | ((period + channel * 5) & 15) for channel in range(3))
        writes.extend([cycle + 8 + i * 32, byte] for i, byte in enumerate(values))
        effective = period or 1024
        # Include the preceding retained counter plus two complete new periods.
        # Three channels sweep together; their bytes and attenuation differ.
        cycle += 128 * (4 * max(previous, effective)) + 512
        previous = effective
    return {"cycles": cycle, "writes": writes}


def noise_fixture():
    return {
        "cycles": (1 << 27) + (1 << 19),
        "writes": [[8, 0xE4], [40, 0xF0], [(1 << 27) + 8, 0xE0]],
    }


def linked_fixture():
    writes, cycle, previous = [], 0, 1024
    for period in range(1024):
        periods = [period ^ 0x155, 1023 - period, period]
        maximum = max(p or 1024 for p in periods)
        for white in [False, True]:
            values = []
            for channel, value in enumerate(periods):
                values.extend([0x80 | (channel << 5) | (value & 15), value >> 4])
            values.extend(
                0x90 | (channel << 5) | ((period + channel * 5) & 15) for channel in range(3)
            )
            values.extend([0xE3 | (4 if white else 0), 0xF0 | (period & 15)])
            writes.extend([cycle + 8 + i * 32, byte] for i, byte in enumerate(values))
            cycle += 128 * (4 * max(previous, maximum)) + 512
            previous = maximum
    return {"cycles": cycle, "writes": writes}


def linked_zero_fixture():
    writes, cycle = [], 0
    for white in [False, True]:
        for phase in [0, 1, 31, 63, 64, 65, 95, 127]:
            values = [(8, 0xE0), (40, 0xF0), (72, 0xC0), (104, 0)]
            # A preceding 1023-count interval can outlast a short case. Let
            # that retained counter finish before testing the next zero entry.
            settle = 1 << 17
            values.append((settle + 256 + phase, 0xE3 | (4 if white else 0)))
            # Leave zero with a retained counter, then return through a
            # nonzero high nibble. Exercise writes around both half ticks.
            values.extend(
                [
                    (settle + 2048 + phase, 0xC1),
                    (settle + 4096 + phase, 0xCF),
                    (settle + 4128 + phase, 63),
                    (settle + 6144 + phase, 0xC0),
                    (settle + 6176 + phase, 0),
                ]
            )
            writes.extend([cycle + offset, byte] for offset, byte in values)
            cycle += 1 << 19
    return {"cycles": cycle, "writes": writes}


def clock_write_fixture():
    writes, cycle = [], 0
    for phase in [0, 1, 31, 63, 64, 65, 95, 127]:
        values = []
        for channel in range(3):
            values.extend([0x81 | (channel << 5), 0, 0x90 | (channel << 5)])
        writes.extend([cycle + 8 + i * 32, byte] for i, byte in enumerate(values))
        # Complete any previous zero-period interval through real clocks.
        settle = 1 << 17
        for group, (low, high) in enumerate([(2, 0), (15, 63), (0, 0), (1, 0)]):
            for channel in range(3):
                at = cycle + settle + 191 + phase + group * 4096 + channel * 256
                writes.extend([[at, 0x80 | (channel << 5) | low], [at + 32, high]])
        cycle += 1 << 19
    return {"cycles": cycle, "writes": writes}


def noise_control_fixture():
    writes, cycle = [], 0
    for control in range(16):
        for phase in [0, 1, 31, 63, 64, 65, 95, 127]:
            values = [
                (8, 0xE3),
                (40, 0xF0),
                (16384 + 63 + phase, 0xE0 | control),
                (17408 + 63 + phase, control ^ 4),
                (20480 + 63 + phase, 0xE3),
            ]
            writes.extend([cycle + offset, byte] for offset, byte in values)
            cycle += 32768
    return {"cycles": cycle, "writes": writes}


def qualify(scope="tones"):
    fixtures = {
        "tones": tone_fixture,
        "noise": noise_fixture,
        "linked": linked_fixture,
        "linked-zero": linked_zero_fixture,
        "clock-writes": clock_write_fixture,
        "noise-controls": noise_control_fixture,
    }
    if scope not in fixtures:
        raise ValueError("Unknown PSG sweep")
    verify()
    inputs = audio_probe.build_probe()
    directory = build.BBC / ("psg-sweep-" + scope)
    directory.mkdir(exist_ok=True)
    fixture = fixtures[scope]()
    write_json(directory / "fixture.json", fixture)
    (directory / "writes.tsv").write_text("".join(f"{c} {b}\n" for c, b in fixture["writes"]))
    script = ROOT / "tools/bbc/reference_psg_sweep.mjs"
    dependencies = [
        Path(__file__),
        script,
        ROOT / "tools/bbc/reference_audio.mjs",
        source_root("jsbeeb") / "src/soundchip.js",
        audio_probe.BINARY,
        directory / "fixture.json",
        directory / "writes.tsv",
        *[ROOT / name for name in inputs["sources"]],
    ]
    before = build.hashes(dependencies)
    started = time.monotonic()
    with (directory / "dut.log").open("w") as log:
        subprocess.run(
            [
                str(audio_probe.BINARY),
                str(directory / "dut.u8"),
                str(directory / "writes.tsv"),
                str(fixture["cycles"]),
            ],
            stdout=log,
            stderr=subprocess.STDOUT,
            check=True,
            timeout=300,
        )
    dut_seconds = time.monotonic() - started
    started = time.monotonic()
    with (directory / "reference.log").open("w") as log:
        subprocess.run(
            [
                "node",
                str(script),
                str(source_root("jsbeeb")),
                str(directory / "fixture.json"),
                str(directory / "reference.u8"),
            ],
            stdout=log,
            stderr=subprocess.STDOUT,
            check=True,
            timeout=180,
        )
    reference_seconds = time.monotonic() - started
    coverage = json.loads((directory / "reference.u8.coverage.json").read_text())
    captures = {}
    for name, suffix in [("levels", ""), ("pins", ".pins")]:
        expected = (directory / ("reference.u8" + suffix)).read_bytes()
        actual = (directory / ("dut.u8" + suffix)).read_bytes()
        captures[name] = {
            "passed": len(actual) == len(expected) == fixture["cycles"] // 64
            and actual == expected,
            "samples": len(actual),
            "different_samples": sum(a != b for a, b in zip(actual, expected))
            + abs(len(actual) - len(expected)),
        }
    complete = {
        "tones": coverage["tone_periods"] == [1024, 1024, 1024],
        "noise": coverage["white_states"] == 32767 and coverage["periodic_states"] == 15,
        "linked": coverage["linked_periods"] == [1024, 1024]
        and coverage["tone_periods"] == [1024, 1024, 1024],
        "linked-zero": all(n >= 4 for n in coverage["linked_periods"])
        and coverage["white_states"] > 0
        and coverage["periodic_states"] == 15,
        "clock-writes": all(n >= 5 for n in coverage["tone_periods"]),
        "noise-controls": coverage["noise_controls"] == 8,
    }[scope]
    if before != build.hashes(dependencies) or not build.generation_current():
        raise ValueError("PSG sweep inputs changed during comparison")
    result = {
        "passed": complete and all(c["passed"] for c in captures.values()),
        "scope": scope,
        "fixture_cycles": fixture["cycles"],
        "clock_hz": 32000000,
        "host_seconds": {"dut": round(dut_seconds, 3), "reference": round(reference_seconds, 3)},
        "captures": captures,
        "reference_coverage": coverage,
        "coverage_complete": complete,
        "inputs": inputs,
        "sources": before,
        "artifacts": build.hashes(list(directory.glob("*.u8*"))),
        "method": "All actual standalone PSG master edges execute. The unchanged original generators supply every expected digital sample and pin vector. Read-only wrappers count original LFSR states; ordinary public byte writes program all periods. No counter phase or expected state is injected.",
        "limits": "Declared common reset/divider profile. Finite tone/attenuation sweep, fixed-rate noise cycles or both linked-noise modes at each divider. Arbitrary protocol sequences, independent channel-period products, sound-bus integration and physical sound remain separate.",
    }
    write_json(directory / "result.json", result)
    print(f"PSG {scope}: {captures}; coverage {coverage}", flush=True)
    if not result["passed"]:
        raise ValueError("PSG sweep differs or coverage is incomplete")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--scope",
        choices=["tones", "noise", "linked", "linked-zero", "clock-writes", "noise-controls"],
        default="tones",
    )
    qualify(parser.parse_args().scope)
