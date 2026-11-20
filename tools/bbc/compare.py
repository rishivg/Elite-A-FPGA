"""Strict released-game comparisons for the independent BBC RTL runtime."""

from __future__ import annotations

import gzip
import json
import re
import zlib
from pathlib import Path

import numpy as np
from PIL import Image

from tools.validation.common import decode_reference, reference_symbols, source_root, write_json
from tools.validation.compare import event_diff, field, pixel_diff, timed_events
from tools.validation.setup import disc_files

from . import storage
from .inputs import analogue_transitions, transitions


def captured_inputs(directory, scenario, ds):
    expected = transitions(scenario)
    actual = []
    for line in (directory / "dut/inputs.tsv").read_text().splitlines():
        parts = line.split()
        if len(parts) != 4:
            raise ValueError("Malformed RTL keyboard capture")
        row = list(map(int, parts))
        if not (
            0 <= row[0] < scenario["duration_us"] * 32
            and 0 <= row[1] < 16
            and 0 <= row[2] < 8
            and row[3] in [0, 1]
        ):
            raise ValueError("Invalid RTL keyboard capture")
        actual.append(row)
    reference = json.loads((directory / "reference/inputs.json").read_text())
    events = scenario.get("events", [])
    reference_ok = len(reference) == len(events)
    for capture, event in zip(reference, events):
        requested = event["at_us"] * 2
        reference_ok &= (
            capture["requested_cycle"] == requested
            and capture["cycle"] == requested
            and capture["keys"] == event["keys"]
        )
    dut_ok = actual == expected and ds["inputs"] == len(actual)
    return {
        "passed": bool(dut_ok and reference_ok),
        "dut_passed": dut_ok,
        "reference_passed": bool(reference_ok),
        "expected_matrix_transitions": len(expected),
        "captured_matrix_transitions": len(actual),
        "reference_events": len(reference),
    }


def captured_analogue(directory, scenario, ds):
    expected = analogue_transitions(scenario)
    actual = []
    for line in (directory / "dut/analogue.tsv").read_text().splitlines():
        parts = line.split()
        if len(parts) != 6 or any(not p.isdecimal() for p in parts):
            raise ValueError("Malformed RTL analogue capture")
        actual.append(list(map(int, parts)))
    captures = json.loads((directory / "reference/analogue.json").read_text())
    reference = []
    for capture in captures:
        if capture["requested_cycle"] != capture["cycle"]:
            raise ValueError("Reference analogue input missed its requested clock")
        reference.append([capture["cycle"] * 16, *capture["channels"], capture["buttons"]])
    dut_ok = actual == expected and ds["analogue_inputs"] == len(expected)
    reference_ok = reference == expected
    return {
        "passed": bool(dut_ok and reference_ok),
        "dut_passed": dut_ok,
        "reference_passed": reference_ok,
        "expected_events": len(expected),
        "captured_dut_events": len(actual),
        "captured_reference_events": len(reference),
    }


def captured_frames(directory, rs, ds):
    captures = {}
    for backend, summary in [("reference", rs), ("dut", ds)]:
        rows = [int(line) for line in (directory / backend / "frames.tsv").read_text().splitlines()]
        if (
            len(rows) != summary["frames"]
            or any(c <= 0 or c > summary["duration_cycles"] for c in rows)
            or any(a >= b for a, b in zip(rows, rows[1:]))
        ):
            raise ValueError("Missing, duplicated or invalid video frame capture")
        captures[backend] = rows
    # Convert reference cycles to the DUT's clock units before comparing field timing.
    expected = [cycle * 16 for cycle in captures["reference"]]
    actual = captures["dut"]
    first = next((i for i, (r, d) in enumerate(zip(expected, actual)) if r != d), None)
    return {
        "reference": len(expected),
        "dut": len(actual),
        "passed": expected == actual,
        "first_difference": first,
        "first_difference_master_cycles": None
        if first is None
        else actual[first] - expected[first],
    }


def captured_fields(directory, rs, ds):
    captures = {}
    clocks_consistent = True
    for backend, summary in [("reference", rs), ("dut", ds)]:
        times = [int(row) for row in (directory / backend / "frames.tsv").read_text().splitlines()]
        rows = []
        for row in (directory / backend / "field-hashes.tsv").read_text().splitlines():
            parts = row.split()
            if (
                len(parts) != 2
                or not parts[0].isdecimal()
                or not re.fullmatch("[0-9a-f]{64}", parts[1])
            ):
                raise ValueError("Malformed displayed-field digest")
            rows.append([int(parts[0]), parts[1]])
        if len(rows) != summary["frames"]:
            raise ValueError("Missing, extra or mistimed displayed-field digest")
        clocks_consistent &= [r[0] for r in rows] == times
        captures[backend] = rows
    expected = [[clock * 16, hash] for clock, hash in captures["reference"]]
    actual = captures["dut"]
    first = next((i for i, (a, b) in enumerate(zip(expected, actual)) if a != b), None)
    return {
        "passed": bool(clocks_consistent and expected == actual),
        "reference_fields": len(expected),
        "dut_fields": len(actual),
        "first_difference_field": first,
        "aperture": "Every completed 512 x 248 bitmap field, RGB SHA-256; exact field clocks",
    }


def bitmap_text(data: bytes, text: str, column: int, row: int) -> bool:
    """Read a fixed Elite bitmap text location using the pinned MOS ROM font.

    CHAR writes eight rows per character at &6000 + row*256 + column*8.
    This checks actual screen memory, independently of view/status flags. A
    moving ship can obscure text, so scenarios must select an unobscured instant.
    """
    font = (source_root("jsbeeb") / "public/roms/os.rom").read_bytes()
    expected = b"".join(font[(ord(c) - 32) * 8 : (ord(c) - 31) * 8] for c in text)
    start = 0x6000 + row * 256 + column * 8
    return data[start : start + len(expected)] == expected


def game_state(data: bytes) -> dict:
    symbols = reference_symbols("flight")
    result = decode_reference(data, symbols)

    def byte(name):
        return data[symbols[name]]

    result["energy"] = byte("ENERGY")
    result["docking_computer_active"] = bool(byte("auto"))
    result["equipment"] = {
        "ecm": bool(byte("ECM")),
        "escape_pod": bool(byte("ESCP")),
        "hyperspace_unit": bool(byte("BOMB")),
        "galactic_hyperdrive": bool(byte("GHYP")),
        "docking_computer": bool(byte("DKCMP")),
        "energy_unit": bool(byte("ENGY")),
    }
    result["ecm"] = {"countdown": byte("ECMA"), "player_active": bool(byte("ECMP"))}
    reward = int.from_bytes(data[symbols["cmdr_cour"] : symbols["cmdr_cour"] + 2], "little")
    result["delivery"] = {
        "active": bool(reward),
        "reward_tenths": reward,
        "destination": [byte("cmdr_courx"), byte("cmdr_coury")],
    }
    result["screen_text"] = {
        "game_over": bitmap_text(data, "GAME OVER", 12, 12),
        "load_commander": bitmap_text(data, "Load New Commander (Y/N)?", 3, 21),
    }
    # TYPE is the shared zero-page ship workspace selector. In 1.E it holds
    # the encyclopedia card choice; in flight it identifies the current work.
    result["workspace_ship_type"] = byte("TYPE")
    result["selected_coordinates"] = [byte("QQ9"), byte("QQ10")]
    result["distance_tenths_ly"] = int.from_bytes(
        data[symbols["QQ8"] : symbols["QQ8"] + 2], "little"
    )
    result["hyperspace_countdown"] = data[symbols["QQ22"] + 1]
    result["shields"] = [byte("FSH"), byte("ASH")]
    result["temperature"] = {"cabin": byte("CABTMP"), "laser": byte("GNTMP")}
    result["missile_target"] = {"target": byte("MSTG"), "searching": byte("MSAR")}
    result["weapons"] = {"mounts": list(data[symbols["LASER"] : symbols["LASER"] + 4])}
    result["controls"] = {
        "source": "joystick" if byte("JSTK") else "keyboard",
        "roll": byte("JSTX"),
        "pitch": byte("JSTY"),
        "laser_pressed": bool(byte("KY7")),
    }
    if not result["docked"]:
        result["weapons"].update(power=byte("LAS"), cooldown=byte("LASCT"))
        types = data[symbols["FRIN"] : symbols["FRIN"] + 12]
        # Outcome indices are physical ship slots, including empty entries.
        # Filtering empty slots makes later ships move when a planet disappears.
        result["universe"] = [
            {
                "slot": i,
                "type": kind,
                "data": list(data[symbols["K%"] + i * 37 : symbols["K%"] + (i + 1) * 37]),
            }
            for i, kind in enumerate(types)
        ]
        result["stardust"] = {
            name: list(data[symbols[name] : symbols[name] + 18])
            for name in ["SX", "SXL", "SY", "SYL", "SZ", "SZL"]
        }
        result["random_seed"] = list(data[symbols["RAND"] : symbols["RAND"] + 4])
    return result


def captured_discs(directory: Path, scenario: dict) -> dict:
    images = {"reference": {}, "dut": {}}
    files = {"reference": {}, "dut": {}}
    comparisons = []
    for checkpoint in scenario["checkpoints"]:
        name = checkpoint["name"]
        for backend in images:
            data = storage.read_bytes(directory / backend, f"{name}.ssd")
            if len(data) < 512 or len(data) > 204800 or len(data) % 256:
                raise ValueError("Missing or invalid captured SSD")
            if ((data[262] & 3) << 8 | data[263]) != 800:
                raise ValueError("Captured disc changed the 80-track geometry")
            # Sector-image exports can omit unwritten trailing tracks. The
            # declared physical profile has 800 sectors; their contents are zero.
            images[backend][name] = data.ljust(204800, b"\0")
            files[backend][name] = disc_files(images[backend][name])
        expected = images["reference"][name]
        actual = images["dut"][name]
        differences = (
            0
            if expected == actual
            else int(
                np.count_nonzero(
                    np.frombuffer(expected, dtype=np.uint8) != np.frombuffer(actual, dtype=np.uint8)
                )
            )
        )
        comparisons.append(
            {"name": name, "different_bytes": differences, "passed": differences == 0}
        )
    assertions = []
    for assertion in scenario.get("disc_assertions", []):
        for backend in files:
            catalogue = files[backend][assertion["checkpoint"]]
            if "catalogue_count" in assertion:
                actual = len(catalogue)
                passed = actual == assertion["catalogue_count"]
            else:
                filename = assertion["file"]
                content = catalogue.get(filename)
                if "exists" in assertion:
                    actual = content is not None
                    passed = actual == assertion["exists"]
                elif "size" in assertion:
                    actual = None if content is None else len(content)
                    passed = actual == assertion["size"]
                elif "same_as" in assertion:
                    previous = files[backend][assertion["same_as"]].get(filename)
                    actual = content is not None and previous is not None and content == previous
                    passed = actual
                else:
                    raise ValueError("Unknown disc assertion")
            assertions.append({**assertion, "backend": backend, "actual": actual, "passed": passed})
    return {
        "passed": all(c["passed"] for c in comparisons) and all(a["passed"] for a in assertions),
        "checkpoints": comparisons,
        "assertions": assertions,
        "capture": "actual sector images; raw flux is not compared",
    }


def captured_audio(directory, scenario, rs):
    """Check every sample with bounded working memory, retaining exact offsets."""
    reference = directory / "reference"
    dut = directory / "dut"
    floats = reference / "audio.f32"
    digital = reference / "audio-digital.u8"
    emitted = dut / "audio.u8"
    compressed = reference / "audio.f32.gz"
    metadata = rs.get("audio", {})
    if not isinstance(metadata, dict):
        raise ValueError("Missing or invalid audio metadata")
    if compressed.exists():
        if floats.exists() or metadata.get("float_encoding") != "gzip-members":
            raise ValueError("Missing or ambiguous float-audio encoding")
        float_bytes = metadata.get("float_bytes")
        if not isinstance(float_bytes, int) or isinstance(float_bytes, bool) or float_bytes < 0:
            raise ValueError("Missing or invalid audio byte count")
        float_path, open_floats = compressed, gzip.open
    else:
        if metadata.get("float_encoding", "raw") != "raw":
            raise ValueError("Missing or invalid float-audio encoding")
        float_bytes = floats.stat().st_size
        if metadata.get("float_bytes", float_bytes) != float_bytes:
            raise ValueError("Missing or invalid audio byte count")
        float_path, open_floats = floats, open
    reference_samples = float_bytes // 4
    dut_samples = storage.length(dut, emitted.name)
    if (
        float_bytes % 4
        or dut_samples != scenario["duration_us"] // 2
        or abs(reference_samples - rs["duration_cycles"] // 4) > 1
    ):
        raise ValueError("Missing or invalid audio samples")
    if storage.length(reference, digital.name) != reference_samples:
        raise ValueError("Missing or incomplete reference digital audio")
    count = min(reference_samples, dut_samples)
    differences = abs(reference_samples - dut_samples)
    first = None
    # Four MiB of float data and two one-MiB digital chunks at a time.
    chunk_samples = 1 << 20
    try:
        with (
            open_floats(float_path, "rb") as analogue,
            storage.open_capture(reference, digital.name) as original,
            storage.open_capture(dut, emitted.name) as actual,
        ):
            for offset in range(0, reference_samples, chunk_samples):
                samples = min(chunk_samples, reference_samples - offset)
                values = np.frombuffer(analogue.read(samples * 4), dtype="<f4")
                if len(values) != samples or not np.isfinite(values).all():
                    raise ValueError("Missing or invalid audio samples")
                shared = min(samples, max(0, count - offset))
                r = np.frombuffer(original.read(shared), dtype="u1")
                d = np.frombuffer(actual.read(shared), dtype="u1")
                if len(r) != shared or len(d) != shared:
                    raise ValueError("Truncated digital audio during comparison")
                changed = r != d
                mismatches = int(np.count_nonzero(changed))
                differences += mismatches
                if first is None and mismatches:
                    first = offset + int(np.argmax(changed))
            if analogue.read(1):
                raise ValueError("Missing or invalid audio samples: trailing float bytes")
    except (EOFError, gzip.BadGzipFile, zlib.error) as error:
        raise ValueError("Missing or invalid compressed audio samples") from error
    return {
        "reference_samples": reference_samples,
        "dut_samples": dut_samples,
        "digital_waveform": {
            "passed": differences == 0,
            "different_samples": differences,
            "first_difference_sample": first
            if first is not None
            else count
            if differences
            else None,
            "sample_rate_hz": 500000,
            "quantization": "Each original channel rounded to a 0..63 level before summing; no phase/offset fitting",
        },
        "analogue_output": "pending physical output/filter validation",
    }


def compare(directory: Path, scenario: dict) -> dict:
    reference = directory / "reference"
    dut = directory / "dut"
    discs = captured_discs(directory, scenario)
    rs = json.loads((reference / "summary.json").read_text())
    ds = json.loads((dut / "summary.json").read_text())
    if (
        not rs["completed"]
        or not ds["completed"]
        or ds["duration_cycles"] != scenario["duration_us"] * 32
    ):
        raise ValueError("Incomplete RTL capture")
    if not scenario["duration_us"] * 2 <= rs["duration_cycles"] <= scenario["duration_us"] * 2 + 32:
        raise ValueError("Reference execution duration differs")
    if ds["checkpoints"] != len(scenario["checkpoints"]) or len(rs["checkpoints"]) != len(
        scenario["checkpoints"]
    ):
        raise ValueError("Missing checkpoints")
    if ds["frames"] < 2 or rs["frames"] < 2:
        raise ValueError("No complete video frames")
    for capture, checkpoint in zip(rs["checkpoints"], scenario["checkpoints"]):
        target = checkpoint["at_us"] * 2
        if capture["name"] != checkpoint["name"] or not target <= capture["cycle"] <= target + 32:
            raise ValueError("Reference checkpoint timing/name differs")
    inputs = captured_inputs(directory, scenario, ds)
    analogue = captured_analogue(directory, scenario, ds)
    cadence = captured_frames(directory, rs, ds)
    fields = captured_fields(directory, rs, ds)
    checkpoints = []
    states = {"reference": {}, "dut": {}}
    for checkpoint in scenario["checkpoints"]:
        name = checkpoint["name"]
        reference_memory = storage.read_bytes(reference, f"{name}.ram")
        dut_memory = storage.read_bytes(dut, f"{name}.ram")
        r = game_state(reference_memory)
        d = game_state(dut_memory)
        changed_memory = np.flatnonzero(
            np.frombuffer(reference_memory, np.uint8) != np.frombuffer(dut_memory, np.uint8)
        )
        memory = {
            "passed": len(changed_memory) == 0,
            "different_bytes": int(len(changed_memory)),
            "first_difference_address": int(changed_memory[0]) if len(changed_memory) else None,
            "capture": "All 65536 bytes; non-mutating MMIO peeks return FF on both machines",
        }
        for backend, state in [("reference", r), ("dut", d)]:
            states[backend][name] = state
            write_json(directory / backend / f"{name}.state.json", state)
        differences = [
            {"field": k, "reference": r.get(k), "dut": d.get(k)}
            for k in sorted(r.keys() | d.keys())
            if r.get(k) != d.get(k)
        ]
        rgba = storage.read_bytes(reference, f"{name}.rgba")
        if len(rgba) != 1024 * 625 * 4:
            raise ValueError("Truncated reference video")
        expected = (
            np.frombuffer(rgba, np.uint8).reshape(625, 1024, 4)[80:576:2, 264:776:2, :3].copy()
        )
        with Image.open(dut / f"{name}.ppm") as image:
            actual = np.asarray(image.convert("RGB")).copy()
        raw_rgb = storage.read_bytes(dut, f"{name}.rgb")
        if len(raw_rgb) != 1024 * 512 * 3:
            raise ValueError("Truncated emitted RGB capture")
        expected_full = np.frombuffer(rgba, np.uint8).reshape(625, 1024, 4)[80:576:2, 264:776, :3]
        actual_full = np.frombuffer(raw_rgb, np.uint8).reshape(512, 1024, 3)[:248, :512]
        full_video = pixel_diff(expected_full, actual_full)
        video = json.loads((dut / f"{name}.video.json").read_text())
        if video["unsupported"]:
            raise ValueError("Checkpoint uses an unsupported video mode")
        Image.fromarray(expected).save(reference / f"{name}.png")
        Image.fromarray(actual).save(dut / f"{name}.png")
        pixels = pixel_diff(expected, actual)
        if expected.shape == actual.shape:
            Image.fromarray(
                np.concatenate(
                    [
                        expected,
                        actual,
                        np.abs(expected.astype(int) - actual.astype(int)).astype(np.uint8),
                    ],
                    axis=1,
                )
            ).save(directory / f"{name}.comparison.png")
        checkpoints.append(
            {
                "name": name,
                "state_differences": differences,
                "memory": memory,
                "video": pixels,
                "full_video": full_video,
            }
        )
    assertions = []
    for assertion in scenario.get("reference_assertions", []):
        for backend in ["reference", "dut"]:
            try:
                actual = field(states[backend][assertion["checkpoint"]], assertion["field"])
            except (LookupError, TypeError, ValueError) as error:
                raise ValueError(
                    f"Missing outcome field {assertion['field']} at "
                    f"{assertion['checkpoint']} on {backend}"
                ) from error
            if "equals" in assertion:
                passed = actual == assertion["equals"]
            elif "at_least" in assertion:
                passed = isinstance(actual, (int, float)) and actual >= assertion["at_least"]
            else:
                raise ValueError("Unknown game-state assertion")
            assertions.append({**assertion, "backend": backend, "actual": actual, "passed": passed})
    events = event_diff(
        timed_events(reference / "psg.tsv", 2000000), timed_events(dut / "psg.tsv", 32000000)
    )
    audio = captured_audio(directory, scenario, rs)
    return {
        "name": scenario["name"],
        "passed": all(
            not c["state_differences"]
            and c["memory"]["passed"]
            and c["video"]["passed"]
            and c["full_video"]["passed"]
            for c in checkpoints
        )
        and discs["passed"]
        and events["passed"]
        and inputs["passed"]
        and analogue["passed"]
        and cadence["passed"]
        and fields["passed"]
        and audio["digital_waveform"]["passed"]
        and all(a["passed"] for a in assertions),
        "checkpoints": checkpoints,
        "assertions": assertions,
        "disc": discs,
        "inputs": inputs,
        "analogue_inputs": analogue,
        "sound_events": events,
        "cadence": cadence,
        "displayed_fields": fields,
        "audio": audio,
    }
