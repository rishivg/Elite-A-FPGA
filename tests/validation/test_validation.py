"""Mutation checks for comparators and failures of the real capture runner."""

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from tools.validation.blueprints import decode_blueprint, source_geometry
from tools.validation.common import (
    HERE,
    ROOT,
    decode_dut,
    decode_reference,
    digest,
    listing_symbols,
    publish_latest,
)
from tools.validation.compare import STATE_FIELDS, event_diff, pixel_diff, state_diff, timed_events
from tools.validation.psg import pdm_check, transfer_check
from tools.validation.render import (
    case_row,
    raw_transform_pairs,
    verified_result,
    viewer_face_pairs,
)
from tools.validation.render_cases import raw_vertex_pairs
from tools.validation.run import compile_scenario
from tools.validation.setup import disc_files


class ReportPublicationTests(unittest.TestCase):
    def test_latest_link_survives_relocating_the_runs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "capture"
            first, second = root / "runs/first", root / "runs/second"
            first.mkdir(parents=True)
            second.mkdir()
            link = root / "latest"
            publish_latest(link, first)
            self.assertEqual(link.resolve(), first)
            publish_latest(link, second)
            self.assertEqual(link.readlink(), Path("runs/second"))
            relocated = root.rename(root.with_name("relocated"))
            self.assertEqual((relocated / "latest").resolve(), relocated / "runs/second")

    def test_latest_keeps_a_regular_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            link = root / "latest"
            link.write_text("user data")
            with self.assertRaises(FileExistsError):
                publish_latest(link, root)
            self.assertEqual(link.read_text(), "user data")


class ComparisonMutations(unittest.TestCase):
    def test_raw_vertex_chain_rejects_missing_wrong_or_viewer_transforms(self):
        transforms = [
            {"ship": i, "transpose": 1, "expected_result": [i & 255] * 6} for i in range(100)
        ]
        vertices = [{"ship": i, "transform": i, "rotated": [i & 255] * 6} for i in range(100)]
        self.assertEqual(len(raw_vertex_pairs(transforms, vertices)), 100)
        for changes in [
            {"transform": -1},
            {"transform": 100},
            {"ship": 101},
            {"rotated": [255] * 6},
        ]:
            with self.assertRaises(ValueError):
                raw_vertex_pairs(transforms, [{**vertices[0], **changes}, *vertices[1:]])
        with self.assertRaises(ValueError):
            raw_vertex_pairs([{**transforms[0], "transpose": 0}, *transforms[1:]], vertices)
        with self.assertRaises(ValueError):
            raw_vertex_pairs(transforms, vertices[:99])

    def test_raw_orientation_chain_requires_viewer_then_transposed_vertices(self):
        normal = list(range(18))
        transposed = sum((normal[2 * i : 2 * i + 2] for i in [0, 3, 6, 1, 4, 7, 2, 5, 8]), [])
        orientation = {"ship": 1, "raw": [0] * 18, "result": normal}
        viewer = {"ship": 1, "vector": [0] * 6, "matrix": normal, "result": [0] * 6}
        vertex = {**viewer, "matrix": transposed}
        pairs = raw_transform_pairs([orientation], [viewer, vertex])
        self.assertEqual([p["transpose"] for p in pairs], [0, 1])
        with self.assertRaises(ValueError):
            raw_transform_pairs([], [viewer])
        with self.assertRaises(ValueError):
            raw_transform_pairs([orientation], [vertex, viewer])
        with self.assertRaises(ValueError):
            raw_transform_pairs([orientation], [viewer, viewer])
        # Symmetric matrices still require the vertex transpose command.
        orientation = {**orientation, "result": [0] * 18}
        viewer = {**viewer, "matrix": [0] * 18}
        pairs = raw_transform_pairs([orientation], [viewer, viewer])
        self.assertEqual([p["transpose"] for p in pairs], [0, 1])

    def test_viewer_face_chain_cannot_substitute_a_later_vertex_transform(self):
        transforms = [
            {
                "ship": i,
                "vector": [1, 0, 2, 0, 3, 0],
                "matrix": [0] * 18,
                "result": [4, 0x55, 5, 0xAA, 6, 0x7F],
            }
            for i in range(20)
        ]
        faces = [
            {
                "ship": i,
                "face": 0,
                "dot_magnitude": [4, 5, 6],
                "dot_sign": [0, 1, 0],
                "normal": [31, 1, 2, 3],
                "scale": 1,
                "distance": 2,
            }
            for i in range(20)
        ]
        self.assertEqual(len(viewer_face_pairs(transforms, faces)), 20)
        with self.assertRaises(ValueError):
            viewer_face_pairs(transforms[1:], faces)
        bad = [{**transforms[0], "result": [0] * 6}, *transforms[1:], transforms[0]]
        with self.assertRaises(ValueError):
            viewer_face_pairs(bad, faces)
        altered = [{**faces[0], "dot_sign": [1, 1, 0]}, *faces[1:]]
        with self.assertRaises(ValueError):
            viewer_face_pairs(transforms, altered)

    def test_stale_failed_or_modified_renderer_artifacts_fail(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run = root / "run"
            run.mkdir()
            (root / "render-latest").symlink_to(run, target_is_directory=True)
            cases = run / "cases.tsv"
            cases.write_text("original cases\n")
            image = run / "mmio.hex"
            image.write_text("00000013\n")
            value = {
                "passed": True,
                "dut_executed": True,
                "input_hashes": {"rtl": "original"},
                "cases_sha256": digest(cases),
                "artifact_hashes": {"mmio.hex": digest(image)},
            }

            def write(value):
                (run / "result.json").write_text(json.dumps(value))

            with (
                patch("tools.validation.render.CACHE", root),
                patch("tools.validation.render.provenance", return_value={"rtl": "original"}),
            ):
                write(value)
                self.assertTrue(verified_result()[1]["passed"])
                for changes in [
                    {"passed": False},
                    {"dut_executed": False},
                    {"input_hashes": {"rtl": "changed"}},
                ]:
                    write({**value, **changes})
                    with self.assertRaises(ValueError):
                        verified_result()
                write(value)
                cases.write_text("changed cases\n")
                with self.assertRaises(ValueError):
                    verified_result()
                cases.write_text("original cases\n")
                image.write_text("changed image\n")
                with self.assertRaises(ValueError):
                    verified_result()

    def test_reference_overlay_selection_never_uses_docked_flight_addresses(self):
        lines = """.ZP
     0000
.XX18
     0072
S% workspace (docked)
.NAME
     0350
.LL9
     3B34 A9 1F
.ZP
     0000
.XX18
     0072
S% workspace (flight)
.LL9
     4871 A5 8C
.LL86
     49DD B1 22
.ZP
     0000
S% workspace (docked)
.LL9
     9999 FF
""".splitlines()
        self.assertEqual(listing_symbols(lines)["LL9"], 0x3B34)
        self.assertEqual(listing_symbols(lines, "flight")["LL9"], 0x4871)
        self.assertEqual(listing_symbols(lines, "flight")["LL86"], 0x49DD)
        self.assertEqual(listing_symbols(lines, "flight")["NAME"], 0x350)
        with self.assertRaises(ValueError):
            listing_symbols(lines, "unsupported")
        with self.assertRaises(ValueError):
            listing_symbols(lines[:11], "flight")

    def test_renderer_fixture_keeps_signed_zero_and_overflow_side_effects(self):
        item = {
            "normal": [255, 255, 255, 255],
            "dot_magnitude": [255, 255, 255],
            "dot_sign": [1, 0, 1],
            "scale": 0,
            "distance": 31,
            "visibility": 0,
            "next_dot_magnitude": [127, 127, 127],
        }
        self.assertEqual(
            case_row(item), [255, 255, 255, 255, 255, 255, 255, 5, 0, 31, 0, 127, 127, 127]
        )

    def test_flight_scenario_requires_reference_in_space_after_launch(self):
        contract = json.loads((HERE / "scenarios.json").read_text())
        scenario = next(s for s in contract["scenarios"] if s["name"] == "flight-combat")
        compiled = compile_scenario(scenario, contract["actions"])
        launched = next(c for c in compiled["checkpoints"] if c["name"] == "launched")
        self.assertGreaterEqual(launched["at_us"], 8000000)
        self.assertIn(
            {"checkpoint": "launched", "field": "docked", "equals": False},
            compiled["reference_assertions"],
        )

    def test_blueprint_signed_coordinates_and_invalid_offsets_indices_fail(self):
        data = bytearray(40)
        data[3] = 32
        data[4] = 36
        data[8] = 12
        data[9] = 1
        data[12] = 4
        data[20:26] = bytes([5, 6, 7, 0x9F, 0, 0])
        data[26:32] = bytes([8, 9, 10, 0x5F, 0, 0])
        data[32:36] = bytes([31, 0, 0, 4])
        data[36:40] = bytes([0x3F, 1, 2, 3])
        good = decode_blueprint(bytes(data), 0)
        self.assertEqual(good["vertices"][0]["position"], [-5, 6, 7])
        self.assertEqual(good["vertices"][1]["position"], [8, -9, 10])
        self.assertEqual(good["faces"][0]["normal"], [1, 2, -3])
        self.assertEqual(good["edges"][0]["vertices"], [0, 1])
        for position, value in [(3, 39), (8, 13), (12, 5), (35, 8), (33, 2)]:
            altered = data.copy()
            altered[position] = value
            with self.subTest(byte=position), self.assertRaises(ValueError):
                decode_blueprint(bytes(altered), 0)
        with self.assertRaises(ValueError):
            decode_blueprint(bytes(data[:-1]), 0)
        altered = data.copy()
        altered[4] = 39
        unresolved = decode_blueprint(bytes(altered), 0, strict=False)
        self.assertIsNone(unresolved["faces"])
        self.assertFalse(unresolved["complete_geometry"])
        self.assertEqual(unresolved["external_reads"][0]["length"], 4)

    def test_blueprint_source_macros_ignore_removed_code_and_preserve_adjacency(self):
        source = """.SHIP_TEST_VERTICES
 VERTEX -1, 2, 3, 0, 0, 15, 15, 31
\\ VERTEX 99, 99, 99, 0, 0, 0, 0, 0
.SHIP_TEST_EDGES
 EDGE 0, 0, 0, 15, 30
.SHIP_TEST_FACES
 FACE 0, -5, 0, 29
"""
        geometry = source_geometry(source, "SHIP_TEST")
        self.assertEqual(len(geometry["vertices"]), 1)
        self.assertEqual(geometry["edges"][0]["faces"], [0, 15])
        self.assertEqual(geometry["faces"][0]["normal"], [0, -5, 0])
        with self.assertRaises(ValueError):
            source_geometry(source.replace(" FACE 0, -5, 0, 29", " FACE 0, -5"), "SHIP_TEST")

    def test_attenuation_and_missing_audio_samples_fail(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "amplitudes.json").write_text(json.dumps([[63] * 14 + [3, 0]] * 4))
            (root / "registers.tsv").write_text("0 1 1 1 0 14 15 15 15 0 1\n")
            (root / "samples.tsv").write_text("7 3 1 0\n")
            self.assertTrue(transfer_check(root, root, 8)["passed"])
            self.assertFalse(transfer_check(root, root, 16)["passed"])
            (root / "samples.tsv").write_text("7 2 1 0\n")
            self.assertFalse(transfer_check(root, root, 8)["passed"])
            (root / "samples.tsv").write_text("6 3 1 0\n")
            with self.assertRaises(ValueError):
                transfer_check(root, root, 8)

    def test_pdm_density_or_incomplete_duration_fail(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "pdm.json"
            good = {
                "passed": True,
                "cycles": 100,
                "pdm_ones": 24,
                "sample_sum": 6300,
                "min_integral_error": -255,
                "max_integral_error": 0,
            }
            path.write_text(json.dumps(good))
            self.assertTrue(pdm_check(path, 100)["passed"])
            for key, value in [("cycles", 99), ("pdm_ones", 25), ("min_integral_error", -256)]:
                path.write_text(json.dumps({**good, key: value}))
                self.assertFalse(pdm_check(path, 100)["passed"])

    def test_reference_free_hold_counts_tonnes_but_not_precious_cargo(self):
        names = [
            "NAME",
            "CASH",
            "QQ14",
            "GCNT",
            "QQ21",
            "QQ20",
            "TALLY",
            "FIST",
            "NOMSL",
            "cmdr_type",
            "QQ0",
            "QQ1",
            "new_hold",
            "DELTA",
            "ALPHA",
            "BETA",
            "QQ11",
            "QQ12",
            "QQ3",
            "QQ4",
            "QQ6",
            "QQ7",
            "SVC",
        ]
        symbols = {name: i * 32 for i, name in enumerate(names)}
        data = bytearray(65536)
        data[symbols["new_hold"]] = 9
        state = decode_reference(bytes(data), symbols)
        self.assertEqual(state["free_hold"], 8)
        data[symbols["QQ20"] + 1] = 1
        data[symbols["QQ20"] + 13] = 100
        state = decode_reference(bytes(data), symbols)
        self.assertEqual(state["free_hold"], 7)

    def test_every_compared_state_field_can_fail(self):
        reference = {key: 0 for key in STATE_FIELDS}
        self.assertEqual(state_diff(reference, reference.copy()), [])
        for key in STATE_FIELDS:
            changed = reference.copy()
            changed[key] = 1
            with self.subTest(field=key):
                self.assertEqual(state_diff(reference, changed)[0]["field"], key)

    def test_missing_state_field_is_a_failure(self):
        self.assertEqual(len(state_diff({}, {})), len(STATE_FIELDS))

    def test_one_pixel_and_palette_change_fail(self):
        reference = np.zeros((4, 5, 3), dtype=np.uint8)
        self.assertTrue(pixel_diff(reference, reference.copy())["passed"])
        for value in [1, 127, 255]:
            changed = reference.copy()
            changed[2, 3, 1] = value
            result = pixel_diff(reference, changed)
            self.assertFalse(result["passed"])
            self.assertEqual(result["different_pixels"], 1)
            self.assertEqual(result["first_difference_yx"], [2, 3])

    def test_resolution_is_never_hidden_by_resizing(self):
        result = pixel_diff(
            np.zeros((248, 256, 3), dtype=np.uint8), np.zeros((128, 128, 3), dtype=np.uint8)
        )
        self.assertFalse(result["passed"])
        self.assertEqual(result["reason"], "dimensions differ")

    def test_wrong_psg_value_time_or_missing_event_fail(self):
        original = [(250, 0x86), (2000, 0x90)]
        self.assertTrue(event_diff(original, original)["passed"])
        for changed in [[(250, 0x87), (2000, 0x90)], [(251, 0x86), (2000, 0x90)], original[:1], []]:
            self.assertFalse(event_diff(original, changed)["passed"])

    def test_clock_conversion_and_invalid_psg_trace(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "psg.tsv"
            path.write_text("2\t144\n")
            self.assertEqual(timed_events(path, 2000000), [(1000, 144)])
            for content in ["2\t256\n", "2\t144\n1\t144\n", "broken\n"]:
                path.write_text(content)
                with self.assertRaises(ValueError):
                    timed_events(path, 2000000)

    def test_truncated_state_captures_fail(self):
        with self.assertRaises(ValueError):
            decode_reference(bytes(65535), {})
        with self.assertRaises(ValueError):
            decode_dut(bytes(32767), {})

    def test_wrong_key_changes_both_backends_replay(self):
        contract = json.loads((HERE / "scenarios.json").read_text())
        original = contract["scenarios"][0]
        status = compile_scenario(original, contract["actions"])
        altered = json.loads(json.dumps(original))
        altered["events"][0]["actions"] = ["chart"]
        chart = compile_scenario(altered, contract["actions"])
        self.assertNotEqual(status["events"][1]["keys"], chart["events"][1]["keys"])
        self.assertNotEqual(status["events"][1]["input2"], chart["events"][1]["input2"])

    def test_release_times_order_and_unknown_actions_fail(self):
        contract = json.loads((HERE / "scenarios.json").read_text())
        original = contract["scenarios"][0]
        for mutate in [
            lambda x: x["events"].pop(),
            lambda x: x["events"][1].update(at_us=50),
            lambda x: x["events"][0].update(actions=["unknown"]),
        ]:
            altered = json.loads(json.dumps(original))
            mutate(altered)
            with self.assertRaises((ValueError, KeyError)):
                compile_scenario(altered, contract["actions"])

    def test_dfs_truncation_and_catalogue_errors_fail(self):
        with self.assertRaises(ValueError):
            disc_files(bytes(511))
        disk = bytearray(512)
        disk[261] = 1
        with self.assertRaises(ValueError):
            disc_files(disk)
        disk[261] = 8
        disk[8:16] = b"TEST   $"
        disk[268] = 1
        disk[271] = 2
        with self.assertRaises(ValueError):
            disc_files(disk)


@unittest.skipUnless(
    (ROOT / "build/rtl/obj_validate/validate").exists(), "requires make validation-build"
)
class NativeCaptureFailures(unittest.TestCase):
    def execute(self, events, checks, duration="4000000"):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "events").write_text(events)
            (root / "checks").write_text(checks)
            return subprocess.run(
                [
                    str(ROOT / "build/rtl/obj_validate/validate"),
                    str(root / "events"),
                    str(root / "checks"),
                    duration,
                    str(root / "out"),
                ],
                cwd=ROOT / "build/rtl",
                capture_output=True,
                text=True,
            )

    def test_missing_or_malformed_events_fail(self):
        for events in [
            "",
            "hello",
            "-1 0 0\n",
            "0 4294967296 0\n",
            "0 0 0\n0 0 0\n",
            "4000000 0 0\n",
        ]:
            with self.subTest(events=events):
                self.assertNotEqual(self.execute(events, "0 initial\n").returncode, 0)

    def test_missing_duplicate_or_outside_checkpoints_fail(self):
        for checks in ["", "4000000 late\n", "0 same\n1 same\n", "0 ../escape\n"]:
            with self.subTest(checks=checks):
                self.assertNotEqual(self.execute("0 0 0\n", checks).returncode, 0)

    def test_zero_negative_or_overflow_duration_fail(self):
        for duration in ["0", "-1", "18446744073709551616"]:
            self.assertNotEqual(self.execute("0 0 0\n", "0 initial\n", duration).returncode, 0)

    def test_incomplete_capture_with_only_one_frame_fails(self):
        # A formerly accepted partial/nonblank run is not a completed scenario.
        result = self.execute("0 0 0\n", "0 initial\n", "1")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("incomplete DUT capture", result.stderr)


@unittest.skipUnless(
    (ROOT / "build/rtl/obj_validate_psg/validate_psg").exists(), "requires make validation-build"
)
class NativePsgFailures(unittest.TestCase):
    def test_script_rejects_missing_empty_malformed_and_outside_events(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "events"
            binary = str(ROOT / "build/rtl/obj_validate_psg/validate_psg")
            for text in [
                None,
                "",
                "hello\n",
                "-1 0\n",
                "0 256\n",
                "0 0\n0 1\n",
                "10000 0\n",
                "0 0 extra\n",
            ]:
                if text is not None:
                    path.write_text(text)
                with self.subTest(events=text):
                    result = subprocess.run(
                        [binary, "--script", str(path), str(root), "10000"],
                        capture_output=True,
                        text=True,
                    )
                    self.assertNotEqual(result.returncode, 0)
