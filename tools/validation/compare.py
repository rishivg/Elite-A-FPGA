"""Strict comparisons and reviewable diagnostics; missing data never passes."""

from __future__ import annotations

import html
import json
import wave
from pathlib import Path

import numpy as np
from PIL import Image

from .common import decode_dut, decode_reference, write_json

STATE_FIELDS = [
    "commander_name",
    "credits_tenths",
    "fuel_tenths_ly",
    "galaxy",
    "galaxy_seed_bytes",
    "cargo",
    "kills",
    "legal_status",
    "missiles",
    "ship_name",
    "free_hold",
]


def state_diff(reference: dict, dut: dict) -> list[dict]:
    return [
        {"field": key, "reference": reference.get(key), "dut": dut.get(key)}
        for key in STATE_FIELDS
        if key not in reference or key not in dut or reference[key] != dut[key]
    ]


def pixel_diff(reference: np.ndarray, dut: np.ndarray) -> dict:
    if reference.shape != dut.shape:
        return {
            "passed": False,
            "reason": "dimensions differ",
            "reference_shape": list(reference.shape),
            "dut_shape": list(dut.shape),
        }
    different = np.any(reference != dut, axis=2)
    coordinates = np.argwhere(different)
    return {
        "passed": not bool(len(coordinates)),
        "different_pixels": len(coordinates),
        "first_difference_yx": coordinates[0].tolist() if len(coordinates) else None,
    }


def timed_events(path: Path, hz: int) -> list[tuple[int, int]]:
    events = []
    for line in path.read_text().splitlines():
        cycle, value = map(int, line.split())
        # Compare integer nanoseconds, not architecture-specific instruction counts.
        if cycle < 0 or value not in range(256):
            raise ValueError("Invalid PSG trace")
        events.append((cycle * 1_000_000_000 // hz, value))
    if any(events[i][0] > events[i + 1][0] for i in range(len(events) - 1)):
        raise ValueError("PSG timestamps are not monotonic")
    return events


def event_diff(reference, dut) -> dict:
    for index in range(max(len(reference), len(dut))):
        expected = reference[index] if index < len(reference) else None
        actual = dut[index] if index < len(dut) else None
        if expected != actual:
            return {
                "passed": False,
                "first_difference": index,
                "reference": expected,
                "dut": actual,
                "reference_count": len(reference),
                "dut_count": len(dut),
            }
    return {"passed": True, "count": len(reference)}


def field(state, dotted):
    value = state
    for key in dotted.split("."):
        value = value[int(key)] if isinstance(value, list) else value[key]
    return value


def write_wav(path: Path, samples: np.ndarray) -> None:
    # Source-rate files retain waveform/timing. No arbitrary peak normalization.
    pcm = np.clip(samples * 32767, -32768, 32767).astype("<i2")
    with wave.open(str(path), "wb") as wav:
        wav.setparams((1, 2, 500000, len(pcm), "NONE", "not compressed"))
        wav.writeframes(pcm.tobytes())


def compare_scenario(directory: Path, scenario: dict, ref_symbols: dict, dut_symbols: dict) -> dict:
    ref, dut = directory / "reference", directory / "dut"
    rs = json.loads((ref / "summary.json").read_text())
    ds = json.loads((dut / "summary.json").read_text())
    expected_ref = scenario["duration_us"] * 2
    expected_dut = scenario["duration_us"] * 4
    if (
        not rs["completed"]
        or not ds["completed"]
        or ds["duration_cycles"] != expected_dut
        or not expected_ref <= rs["duration_cycles"] <= expected_ref + 32
    ):
        raise ValueError("Incomplete run or incorrect duration")
    if len(rs["checkpoints"]) != len(scenario["checkpoints"]) or ds["checkpoints"] != len(
        scenario["checkpoints"]
    ):
        raise ValueError("Incomplete checkpoint capture")
    states = {"reference": {}, "dut": {}}
    checkpoints = []
    for cp in scenario["checkpoints"]:
        name = cp["name"]
        rstate = decode_reference((ref / f"{name}.ram").read_bytes(), ref_symbols)
        dstate = decode_dut((dut / f"{name}.ram").read_bytes(), dut_symbols)
        states["reference"][name], states["dut"][name] = rstate, dstate
        write_json(ref / f"{name}.state.json", rstate)
        write_json(dut / f"{name}.state.json", dstate)
        rgba = (ref / f"{name}.rgba").read_bytes()
        if len(rgba) != 1024 * 625 * 4:
            raise ValueError("Truncated reference video capture")
        image = Image.frombytes("RGBA", (1024, 625), rgba).convert("RGB")
        image.save(ref / f"{name}.full.png")
        # Pinned BBC Model B Elite-A's 256x248 active area, including the
        # split-mode colour HUD. Remove only the border and fixed 2x scanout.
        pixels = np.asarray(image)[80:576:2, 264:776:2, :].copy()
        Image.fromarray(pixels).save(ref / f"{name}.png")
        with Image.open(dut / f"{name}.pgm") as dimage:
            actual = np.asarray(dimage.convert("RGB")).copy()
        Image.fromarray(actual).save(dut / f"{name}.png")
        video = pixel_diff(pixels, actual)
        # Padding for diagnosis only, never passed off as display equivalence.
        h = max(pixels.shape[0], actual.shape[0])
        w = max(pixels.shape[1], actual.shape[1])
        left = np.zeros((h, w, 3), np.uint8)
        right = left.copy()
        left[: pixels.shape[0], : pixels.shape[1]] = pixels
        right[: actual.shape[0], : actual.shape[1]] = actual
        Image.fromarray(np.concatenate([left, right], axis=1)).save(
            directory / f"{name}.side-by-side.png"
        )
        Image.fromarray(np.abs(left.astype(int) - right.astype(int)).astype(np.uint8)).save(
            directory / f"{name}.diff.png"
        )
        checkpoints.append(
            {
                "name": name,
                "at_us": cp["at_us"],
                "state_differences": state_diff(rstate, dstate),
                "video": video,
            }
        )
    assertions = []
    for backend in ["reference", "dut"]:
        for assertion in scenario.get(backend + "_assertions", []):
            actual = field(states[backend][assertion["checkpoint"]], assertion["field"])
            assertions.append(
                {
                    "backend": backend,
                    **assertion,
                    "actual": actual,
                    "passed": actual == assertion["equals"],
                }
            )
    sound = event_diff(
        timed_events(ref / "psg.tsv", 2000000), timed_events(dut / "psg.tsv", 4000000)
    )
    ra = np.fromfile(ref / "audio.f32", dtype="<f4")
    da = np.fromfile(dut / "audio.u8", dtype="u1").astype(float) / 252
    if (
        len(da) != expected_dut // 8
        or abs(len(ra) - rs["duration_cycles"] // 4) > 1
        or not np.isfinite(ra).all()
    ):
        raise ValueError("Missing, truncated or invalid audio capture")
    write_wav(ref / "audio.wav", ra)
    write_wav(dut / "audio.wav", da)
    # jsbeeb includes DC restoration; raw DUT DAC is unipolar. These waveforms
    # are diagnostic, not an analog equivalence test. Digital event comparison is required.
    result = {
        "name": scenario["name"],
        "passed": False,
        "checkpoints": checkpoints,
        "behavior_assertions": assertions,
        "sound_events": sound,
        "audio": {
            "reference_samples": len(ra),
            "dut_samples": len(da),
            "reference_rms": float(np.sqrt(np.mean(ra**2))),
            "dut_rms": float(np.sqrt(np.mean(da**2))),
            "waveform_equivalence": "unvalidated: DC restoration and DAC transfer differ",
        },
        "unsupported_actions": scenario.get("unsupported_actions", []),
        "limitations": scenario.get("limitations", []),
        "cadence": {
            "reference_video_frames": rs["frames"],
            "dut_presentations": ds["frames"],
            "note": "Physical scanout and game updates are distinct; cadence is captured, not assumed equal",
        },
        "unmapped_state": [
            "docked state (DUT has a temporary dock animation)",
            "roll/pitch/speed units",
            "equipment/world/mission structures",
            "actual serialized DFS save",
        ],
    }
    result["passed"] = (
        bool(checkpoints)
        and all(not c["state_differences"] and c["video"]["passed"] for c in checkpoints)
        and sound["passed"]
        and all(a["passed"] for a in assertions)
        and not result["unsupported_actions"]
        and not result["limitations"]
    )
    write_json(directory / "result.json", result)
    return result


def report(
    directory: Path, results: list[dict], coverage: dict, probes: dict, passed: bool
) -> None:
    parts = [
        '<!doctype html><meta charset="utf-8"><title>Elite-A validation</title>',
        "<style>body{font:16px system-ui;max-width:1100px;margin:2em auto;background:#121721;color:#eee}a{color:#9df}img{image-rendering:pixelated;max-width:100%}pre{white-space:pre-wrap}.fail{color:#ff9e9e}</style>",
        "<h1>Released Elite-A: "
        + ("PASS" if passed else "FAIL")
        + "</h1><p>Independent original binary vs playable Verilated Soc. Only declared checks and coverage count; physical FPGA equivalence requires board capture.</p>",
        '<p><a href="manifest.json">Provenance and hashes</a> · <a href="result.json">Machine-readable results</a> · <a href="reference-blueprints.json">Released ship geometry and metadata</a> · <a href="renderer-kernel.json">Original face kernel validation</a></p>',
    ]
    for result in results:
        name = html.escape(result["name"])
        parts.append(f'<h2 class="fail">{name}: {"PASS" if result["passed"] else "FAIL"}</h2>')
        if "error" in result:
            parts.append("<pre>" + html.escape(result["error"]) + "</pre>")
            continue
        first = result["checkpoints"][0]
        parts.append(
            "<pre>"
            + html.escape(
                json.dumps(
                    {
                        "first_checkpoint": first,
                        "sound": result["sound_events"],
                        "behavior_assertions": result["behavior_assertions"],
                        "unsupported_actions": result["unsupported_actions"],
                        "limitations": result["limitations"],
                    },
                    indent=2,
                )
            )
            + "</pre>"
        )
        for cp in result["checkpoints"]:
            stem = name + "/" + html.escape(cp["name"])
            parts.append(
                f'<details><summary>{html.escape(cp["name"])}</summary><p>Reference left; DUT right, native pixels, padded for viewing.</p><img src="{stem}.side-by-side.png"><p><a href="{stem}.diff.png">Pixel difference</a></p></details>'
            )
        parts.append(
            f'<p><a href="{name}/result.json">Full result</a> · <a href="{name}/reference/audio.wav">Reference audio</a> · <a href="{name}/dut/audio.wav">DUT audio</a> · <a href="{name}/scenario.json">Input replay</a></p>'
        )
    parts.append(
        "<h2>Independent PSG probes: "
        + ("PASS" if probes["passed"] else "FAIL")
        + '</h2><p><a href="psg-probes/result.json">All-channel tone periods, noise rates/LFSR, register protocol, quantized attenuation and digital PDM conservation</a>. Absolute phase, transient divider writes, the tone-2 zero-period reference disagreement and analog filter response remain unresolved.</p>'
    )
    parts.append(
        "<h2>Required coverage still missing</h2><pre>"
        + html.escape(json.dumps(coverage, indent=2))
        + "</pre>"
    )
    (directory / "report.html").write_text("\n".join(parts) + "\n")
