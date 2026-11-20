"""Mutations must fail original-game capture comparisons."""

import json
import subprocess
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier, Event
from unittest.mock import patch

import numpy as np
from PIL import Image

from tools.bbc import system96
from tools.bbc.capture import native_environment
from tools.bbc.compare import captured_frames, compare, game_state
from tools.bbc.profile import artifacts
from tools.bbc.run import (
    capture_matrix,
    capture_pair,
    compile_selected,
    main,
    matrix_evidence,
    summarize_repetitions,
)
from tools.validation.common import ROOT, reference_symbols, source_root, write_json
from tools.validation.compare import field as decoded_field
from tools.validation.run import compile_scenario


class NativeReplayClockTests(unittest.TestCase):
    def test_idle_fall_agrees_with_full_eval_through_warm_requests_and_input_changes(self):
        if not system96.BINARY.is_file() or not system96.current():
            self.skipTest("Requires make bbc-build96")
        if not json.loads(system96.RECEIPT.read_text()).get("idle_falling_edge"):
            self.skipTest("Verilator did not generate the recognized clock trigger")
        machine = system96.DIRECTORY.parent
        with tempfile.TemporaryDirectory() as directory:
            fixture = Path(directory)
            keys = fixture / "keys.tsv"
            keys.write_text("19200 0 2 1\n32000 0 2 0\n")
            analogue = fixture / "analogue.tsv"
            analogue.write_text("19200 1 32768 65535 257 1\n32000 200 100 400 800 0\n")
            restarts = fixture / "restarts.tsv"
            restarts.write_text("19200\n48000\n")
            checkpoints = fixture / "checkpoints.tsv"
            checkpoints.write_text("0 start\n19199 before\n19200 after\n96000 end\n")
            env = native_environment(
                analogue=analogue,
                disc=machine.parent / "disc.ssd",
                restart=restarts,
                trace=True,
                ram_trace=True,
                cold_prefix=True,
            )
            observed = []
            for mode in ("full", "fast"):
                if mode == "full":
                    env["BBC_FULL_EVAL"] = "1"
                else:
                    env.pop("BBC_FULL_EVAL", None)
                output = fixture / mode
                result = subprocess.run(
                    [system96.BINARY, "96000", keys, checkpoints, output],
                    cwd=machine,
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=45,
                )
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertEqual((output / "restarts.tsv").read_text(), restarts.read_text())
                self.assertGreater((output / "ram-writes.tsv").stat().st_size, 0)
                observed.append(artifacts(output))
            self.assertEqual(*observed)


class ReleasedScenarioContractTests(unittest.TestCase):
    def test_every_bundled_input_schedule_compiles(self):
        # Catch missing actions before the longest captures consume minutes.
        shared = json.loads((ROOT / "tools/validation/scenarios.json").read_text())
        bbc = json.loads((ROOT / "tools/bbc/scenarios.json").read_text())
        actions = shared["actions"] | bbc["actions"]
        for scenario in shared["scenarios"] + bbc["scenarios"]:
            with self.subTest(scenario=scenario["name"]):
                compile_scenario(scenario, actions)

    def test_bundled_outcomes_name_real_checkpoints_and_decoded_fields(self):
        # A misspelled outcome should fail in seconds, before guest replay.
        ram = bytearray(65536)
        symbols = reference_symbols("flight")
        ram[symbols["FRIN"] : symbols["FRIN"] + 12] = bytes([1] * 12)
        states = [game_state(ram)]
        ram[symbols["QQ12"]] = 255
        states.append(game_state(ram))
        for name in ["tools/validation/scenarios.json", "tools/bbc/scenarios.json"]:
            contract = json.loads((ROOT / name).read_text())
            for scenario in contract["scenarios"]:
                checkpoints = {c["name"] for c in scenario["checkpoints"]}
                for assertion in scenario.get("reference_assertions", []):
                    with self.subTest(scenario=scenario["name"], assertion=assertion):
                        self.assertIn(assertion["checkpoint"], checkpoints)
                        decoded = False
                        for state in states:
                            try:
                                decoded_field(state, assertion["field"])
                                decoded = True
                                break
                            except (KeyError, IndexError, TypeError):
                                pass
                        self.assertTrue(decoded, "Unknown decoded game-state field")


class ScenarioPreflightTests(unittest.TestCase):
    def contract(self):
        return {
            "actions": {"fire": {"keys": ["A"]}},
            "scenarios": [
                {
                    "name": "combat",
                    "duration_us": 1000,
                    "events": [
                        {"at_us": 100, "actions": ["fire"]},
                        {"at_us": 200, "actions": []},
                    ],
                    "checkpoints": [{"at_us": 500, "name": "end"}],
                }
            ],
        }

    def test_unknown_selection_fails_before_verification_or_boot(self):
        with (
            patch("sys.argv", ["bbc", "--scenario", "unknown-case"]),
            patch("tools.bbc.run.verify") as verify,
            patch("tools.bbc.run.native_current") as native,
        ):
            with self.assertRaisesRegex(ValueError, "Unknown scenario"):
                main()
            verify.assert_not_called()
            native.assert_not_called()

    def test_missing_action_names_the_case_before_backend_work(self):
        with (
            patch("sys.argv", ["bbc", "--scenario", "equipment"]),
            patch("tools.bbc.run.compile_scenario", side_effect=KeyError("missing-key")),
            patch("tools.bbc.run.verify") as verify,
        ):
            with self.assertRaisesRegex(ValueError, "Invalid scenario equipment.*missing-key"):
                main()
            verify.assert_not_called()

    def test_invalid_analogue_clock_fails_during_preflight(self):
        contract = self.contract()
        contract["scenarios"][0]["analogue"] = [
            {"at_us": 1000, "channels": [32768] * 4, "buttons": 0}
        ]
        with self.assertRaisesRegex(ValueError, "Invalid scenario combat.*analogue"):
            compile_selected(contract)

    def test_unselected_invalid_case_does_not_block_targeted_capture(self):
        contract = self.contract()
        contract["scenarios"].append({"name": "unfinished"})
        selected = compile_selected(contract, ["combat"])
        self.assertEqual([s["name"] for s in selected], ["combat"])
        self.assertEqual(selected[0]["events"][1]["keys"], ["A"])
        self.assertNotIn("keys", contract["scenarios"][0]["events"][0])


class CapturePairTests(unittest.TestCase):
    def test_matrix_requires_unique_repeatable_cases_and_provenance(self):
        rows = [{"name": "first", "passed": True, "repeatable": True}]
        options = {"boot_provenance_complete": True, "inputs_unchanged": True}
        self.assertTrue(matrix_evidence(rows, ["first"], **options)["passed"])
        missing = matrix_evidence(rows, ["first", "second"], **options)
        self.assertFalse(missing["passed"])
        self.assertEqual(missing["scenario_coverage"]["missing"], ["second"])
        self.assertFalse(matrix_evidence(rows * 2, ["first"], **options)["passed"])
        self.assertFalse(
            matrix_evidence(rows, ["first"], **(options | {"inputs_unchanged": False}))["passed"]
        )
        rows[0]["repeatable"] = False
        self.assertFalse(matrix_evidence(rows, ["first"], **options)["passed"])

    def test_failed_second_capture_fails_case_and_keeps_error(self):
        results = [
            {"passed": True, "artifact_hashes": {"dut": "same", "reference": "same"}},
            {"passed": False, "error": "reference capture failed"},
        ]
        summary = summarize_repetitions(results)
        self.assertFalse(summary["passed"])
        self.assertFalse(summary["repeatable"])
        self.assertEqual(summary["repetitions"][1]["error"], "reference capture failed")

    def test_matching_comparisons_with_changed_capture_hashes_fail_case(self):
        results = [
            {"passed": True, "artifact_hashes": {"dut": "first", "reference": "first"}},
            {"passed": True, "artifact_hashes": {"dut": "second", "reference": "second"}},
        ]
        self.assertFalse(summarize_repetitions(results)["passed"])
        results[1]["artifact_hashes"] = dict(results[0]["artifact_hashes"])
        self.assertTrue(summarize_repetitions(results)["passed"])

    def test_longest_first_retains_scenario_and_repetition_order(self):
        scenarios = [{"name": "short", "duration_us": 1}, {"name": "long", "duration_us": 10}]
        started = []

        def capture(scenario, repetition, executor):
            key = (scenario["name"], repetition)
            started.append(key)
            return key

        completed = []
        results = capture_matrix(scenarios, 2, 1, capture, on_progress=completed.append)
        self.assertEqual(started, [("long", 0), ("long", 1), ("short", 0), ("short", 1)])
        self.assertEqual(results, [[("short", 0), ("short", 1)], [("long", 0), ("long", 1)]])
        self.assertEqual(completed, list(reversed(results)))

    def test_matrix_schedules_only_missing_repetitions_and_reports_completed_pairs(self):
        scenarios = [{"name": "archived", "duration_us": 1}, {"name": "partial", "duration_us": 10}]
        started = []

        def capture(scenario, repetition, executor):
            started.append((scenario["name"], repetition))
            return "new"

        completed = []
        result = capture_matrix(
            scenarios,
            2,
            2,
            capture,
            on_progress=completed.append,
            recovered={
                ("archived", 0): "archived",
                ("archived", 1): "archived",
                ("partial", 1): "archived",
            },
        )
        self.assertEqual(started, [("partial", 0)])
        self.assertEqual(result, [["archived", "archived"], ["new", "archived"]])
        self.assertEqual(completed, result)

    def test_repetitions_overlap_without_exceeding_backend_limit(self):
        from threading import Lock

        repetitions = Barrier(2)
        backends = Barrier(2)
        lock = Lock()
        active = 0
        maximum = 0

        def backend():
            nonlocal active, maximum
            with lock:
                active += 1
                maximum = max(maximum, active)
            try:
                backends.wait(timeout=5)
            finally:
                with lock:
                    active -= 1

        def capture(scenario, repetition, executor):
            repetitions.wait(timeout=5)
            capture_pair(executor, backend, backend)
            return repetition

        results = capture_matrix([{"duration_us": 10}], 2, 2, capture)
        self.assertEqual(results, [[0, 1]])
        self.assertEqual(maximum, 2)

    def test_backends_can_run_concurrently(self):
        barrier = Barrier(2)
        finished = []

        def capture(name):
            barrier.wait(timeout=5)
            finished.append(name)

        with ThreadPoolExecutor(max_workers=2) as executor:
            capture_pair(executor, lambda: capture("dut"), lambda: capture("reference"))
        self.assertCountEqual(finished, ["dut", "reference"])

    def test_failure_waits_for_other_backend_and_propagates(self):
        barrier = Barrier(2)
        failed = Event()
        finished = Event()

        def dut():
            barrier.wait(timeout=5)
            failed.set()
            raise subprocess.CalledProcessError(1, ["dut"])

        def reference():
            barrier.wait(timeout=5)
            if not failed.wait(timeout=5):
                raise RuntimeError("DUT did not complete")
            finished.set()

        with ThreadPoolExecutor(max_workers=2) as executor:
            with self.assertRaises(subprocess.CalledProcessError):
                capture_pair(executor, dut, reference)
            self.assertTrue(finished.is_set(), "failed capture returned before its peer completed")


class BbcValidationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.scenario = {
            "name": "fixture",
            "duration_us": 50000,
            "events": [{"at_us": 10000, "keys": ["F8"]}, {"at_us": 20000, "keys": []}],
            "checkpoints": [{"at_us": 0, "name": "initial"}, {"at_us": 40000, "name": "end"}],
        }
        symbols = reference_symbols()
        ram = bytearray(65536)
        ram[symbols["NAME"] : symbols["NAME"] + 8] = b"NEWCOME\r"
        ram[symbols["QQ12"]] = 128
        for backend in ["reference", "dut"]:
            directory = self.root / backend
            directory.mkdir()
            write_json(
                directory / "summary.json",
                {
                    "completed": True,
                    "duration_cycles": 100000 if backend == "reference" else 1600000,
                    "frames": 2,
                    "inputs": 2,
                    "analogue_inputs": 0,
                    "checkpoints": [
                        {"name": c["name"], "cycle": c["at_us"] * 2}
                        for c in self.scenario["checkpoints"]
                    ]
                    if backend == "reference"
                    else 2,
                },
            )
            (directory / "psg.tsv").write_text("")
            (directory / "frames.tsv").write_text(
                "40000\n80000\n" if backend == "reference" else "640000\n1280000\n"
            )
            import hashlib

            field_hash = hashlib.sha256(bytes(512 * 248 * 3)).hexdigest()
            (directory / "field-hashes.tsv").write_text(
                "".join(
                    f"{clock} {field_hash}\n"
                    for clock in ([40000, 80000] if backend == "reference" else [640000, 1280000])
                )
            )
            for checkpoint in self.scenario["checkpoints"]:
                name = checkpoint["name"]
                (directory / f"{name}.ram").write_bytes(ram)
                disc = bytearray(204800)
                disc[262] = 3
                disc[263] = 32
                (directory / f"{name}.ssd").write_bytes(disc)
                if backend == "reference":
                    (directory / f"{name}.rgba").write_bytes(bytes(1024 * 625 * 4))
                else:
                    (directory / f"{name}.rgb").write_bytes(bytes(1024 * 512 * 3))
                    Image.fromarray(np.zeros((248, 256, 3), np.uint8)).save(
                        directory / f"{name}.ppm"
                    )
                    write_json(directory / f"{name}.video.json", {"unsupported": False})
        (self.root / "reference/audio.f32").write_bytes(bytes(25000 * 4))
        (self.root / "dut/audio.u8").write_bytes(bytes(25000))
        (self.root / "reference/audio-digital.u8").write_bytes(bytes(25000))
        (self.root / "dut/analogue.tsv").write_text("")
        write_json(self.root / "reference/analogue.json", [])
        (self.root / "dut/inputs.tsv").write_text("320000 6 7 1\n640000 6 7 0\n")
        write_json(
            self.root / "reference/inputs.json",
            [
                {"requested_cycle": e["at_us"] * 2, "cycle": e["at_us"] * 2, "keys": e["keys"]}
                for e in self.scenario["events"]
            ],
        )

    def test_matching_capture_and_empty_sound_stream(self):
        self.assertTrue(compare(self.root, self.scenario)["passed"])

    def test_phase_specific_missing_field_names_checkpoint_and_backend(self):
        # Universe is a valid flight field, but these actual captures are docked.
        self.scenario["reference_assertions"] = [
            {"checkpoint": "end", "field": "universe.2.type", "equals": 16}
        ]
        with self.assertRaisesRegex(
            ValueError, "Missing outcome field universe.2.type at end on reference"
        ):
            compare(self.root, self.scenario)

    def test_empty_ship_slots_preserve_physical_indices_when_ships_disappear(self):
        symbols = reference_symbols("flight")
        ram = bytearray(65536)
        ram[symbols["FRIN"] + 1] = 2
        ram[symbols["FRIN"] + 2] = 16
        ram[symbols["K%"] + 2 * 37 + 35] = 91
        ships = game_state(ram)["universe"]
        self.assertEqual(len(ships), 12)
        self.assertEqual(ships[0]["type"], 0)
        self.assertEqual(ships[2]["slot"], 2)
        self.assertEqual(ships[2]["type"], 16)
        self.assertEqual(ships[2]["data"][35], 91)
        ram[symbols["FRIN"] + 2] = 0
        ram[symbols["FRIN"] + 3] = 19
        ships = game_state(ram)["universe"]
        self.assertEqual(ships[2]["type"], 0)
        self.assertEqual(ships[3]["slot"], 3)
        self.assertEqual(ships[3]["type"], 19)

    def test_changed_field_between_checkpoints_fails(self):
        p = self.root / "dut/field-hashes.tsv"
        rows = p.read_text().splitlines()
        parts = rows[0].split()
        parts[1] = "0" * 64
        rows[0] = " ".join(parts)
        p.write_text("\n".join(rows) + "\n")
        result = compare(self.root, self.scenario)
        self.assertFalse(result["passed"])
        self.assertEqual(result["displayed_fields"]["first_difference_field"], 0)
        self.assertTrue(all(c["full_video"]["passed"] for c in result["checkpoints"]))

    def test_matching_memory_cannot_hide_the_wrong_ship_card_outcome(self):
        self.scenario["reference_assertions"] = [
            {"checkpoint": "end", "field": "workspace_ship_type", "equals": 0}
        ]
        self.assertTrue(compare(self.root, self.scenario)["passed"])
        for backend in ["reference", "dut"]:
            path = self.root / backend / "end.ram"
            data = bytearray(path.read_bytes())
            data[reference_symbols("flight")["TYPE"]] = 1
            path.write_bytes(data)
        result = compare(self.root, self.scenario)
        self.assertFalse(result["passed"])
        self.assertTrue(all(not item["passed"] for item in result["assertions"]))

    def test_opponent_ecm_does_not_count_as_player_activation(self):
        self.scenario["reference_assertions"] = [
            {"checkpoint": "end", "field": "ecm.player_active", "equals": True}
        ]
        symbols = reference_symbols("flight")
        for backend in ["reference", "dut"]:
            path = self.root / backend / "end.ram"
            data = bytearray(path.read_bytes())
            data[symbols["ECM"]] = 255
            data[symbols["ECMA"]] = 24
            path.write_bytes(data)
        self.assertFalse(compare(self.root, self.scenario)["passed"])
        for backend in ["reference", "dut"]:
            path = self.root / backend / "end.ram"
            data = bytearray(path.read_bytes())
            data[symbols["ECMP"]] = 255
            path.write_bytes(data)
        self.assertTrue(compare(self.root, self.scenario)["passed"])

    def test_death_flags_cannot_substitute_for_actual_game_over_text(self):
        data = bytearray(65536)
        symbols = reference_symbols("flight")
        data[symbols["QQ11"]] = 6
        data[symbols["LASCT"]] = 255
        data[symbols["FRIN"] : symbols["FRIN"] + 5] = bytes([5] * 5)
        self.assertFalse(game_state(data)["screen_text"]["game_over"])

    def test_screen_outcomes_require_exact_original_font_glyphs(self):
        data = bytearray(65536)
        font = (source_root("jsbeeb") / "public/roms/os.rom").read_bytes()
        for field, text, start in [
            ("game_over", "GAME OVER", 0x6C60),
            ("load_commander", "Load New Commander (Y/N)?", 0x7518),
        ]:
            glyphs = b"".join(font[(ord(c) - 32) * 8 : (ord(c) - 31) * 8] for c in text)
            data[start : start + len(glyphs)] = glyphs
            self.assertTrue(game_state(data)["screen_text"][field])
            data[start + 1] ^= 1
            self.assertFalse(game_state(data)["screen_text"][field])

    def test_matching_memory_cannot_hide_a_missing_delivery_penalty(self):
        self.scenario["reference_assertions"] = [
            {"checkpoint": "end", "field": "delivery.reward_tenths", "equals": 5106}
        ]
        symbols = reference_symbols("flight")
        for reward, expected in [(10212, False), (5106, True)]:
            for backend in ["reference", "dut"]:
                path = self.root / backend / "end.ram"
                data = bytearray(path.read_bytes())
                start = symbols["cmdr_cour"]
                data[start : start + 2] = reward.to_bytes(2, "little")
                path.write_bytes(data)
            self.assertEqual(compare(self.root, self.scenario)["passed"], expected)

    def test_delivery_with_zero_low_reward_byte_is_still_active(self):
        data = bytearray(65536)
        symbols = reference_symbols("flight")
        data[symbols["cmdr_cour"] + 1] = 1
        data[symbols["cmdr_courx"]] = 49
        data[symbols["cmdr_coury"]] = 119
        self.assertEqual(
            game_state(data)["delivery"],
            {"active": True, "reward_tenths": 256, "destination": [49, 119]},
        )

    def test_matching_memory_cannot_substitute_classic_legal_decay(self):
        self.scenario["reference_assertions"] = [
            {"checkpoint": "end", "field": "legal_status", "equals": 59}
        ]
        address = reference_symbols("flight")["FIST"]
        for legal, expected in [(32, False), (59, True)]:
            for backend in ["reference", "dut"]:
                path = self.root / backend / "end.ram"
                data = bytearray(path.read_bytes())
                data[address] = legal
                path.write_bytes(data)
            self.assertEqual(compare(self.root, self.scenario)["passed"], expected)

    def test_kill_credit_does_not_replace_ranged_enemy_destruction(self):
        # A missile impact can also increment TALLY. Require the ship's actual
        # explosion, energy underflow and distant location on both machines.
        self.scenario["reference_assertions"] = [
            {"checkpoint": "end", "field": field, "equals": value}
            for field, value in [
                ("kills", 1),
                ("universe.2.data.31", 105),
                ("universe.2.data.35", 253),
                ("universe.2.data.6", 235),
                ("universe.2.data.7", 22),
                ("universe.2.data.8", 128),
            ]
        ]
        symbols = reference_symbols("flight")
        for backend in ["reference", "dut"]:
            path = self.root / backend / "end.ram"
            data = bytearray(path.read_bytes())
            data[symbols["TALLY"]] = 1
            data[symbols["QQ12"]] = 0
            data[symbols["FRIN"] : symbols["FRIN"] + 3] = bytes([128, 2, 16])
            ship = symbols["K%"] + 74
            data[ship + 31] = 105
            data[ship + 35] = 253
            data[ship + 6 : ship + 9] = bytes([235, 22, 128])
            path.write_bytes(data)
        self.assertTrue(compare(self.root, self.scenario)["passed"])
        for offset, wrong in [(31, 25), (35, 4), (7, 0)]:
            originals = {}
            for backend in ["reference", "dut"]:
                path = self.root / backend / "end.ram"
                originals[path] = path.read_bytes()
                data = bytearray(originals[path])
                data[ship + offset] = wrong
                path.write_bytes(data)
            self.assertFalse(compare(self.root, self.scenario)["passed"])
            for path, data in originals.items():
                path.write_bytes(data)

    def test_missing_or_malformed_field_digest_fails(self):
        p = self.root / "dut/field-hashes.tsv"
        original = p.read_text()
        for invalid in ["", original.splitlines()[0] + "\n", "640000 invalid\n"]:
            p.write_text(invalid)
            with self.assertRaises(ValueError):
                compare(self.root, self.scenario)
        p.unlink()
        with self.assertRaises(OSError):
            compare(self.root, self.scenario)

    def test_scratch_memory_difference_is_not_hidden_by_matching_game_state(self):
        p = self.root / "dut/end.ram"
        data = bytearray(p.read_bytes())
        data[0x1F0] = 1
        p.write_bytes(data)
        result = compare(self.root, self.scenario)
        self.assertFalse(result["passed"])
        self.assertEqual(result["checkpoints"][1]["state_differences"], [])
        self.assertEqual(result["checkpoints"][1]["memory"]["first_difference_address"], 0x1F0)

    def test_odd_emitted_pixel_is_not_hidden_by_downsampled_image(self):
        p = self.root / "dut/end.rgb"
        data = bytearray(p.read_bytes())
        data[3] = 255
        p.write_bytes(data)
        result = compare(self.root, self.scenario)
        self.assertFalse(result["passed"])
        self.assertTrue(result["checkpoints"][1]["video"]["passed"])
        self.assertEqual(result["checkpoints"][1]["full_video"]["different_pixels"], 1)

    def test_missing_or_truncated_emitted_rgb_cannot_pass(self):
        p = self.root / "dut/end.rgb"
        p.write_bytes(bytes(12))
        with self.assertRaises(ValueError):
            compare(self.root, self.scenario)
        p.unlink()
        with self.assertRaises(OSError):
            compare(self.root, self.scenario)

    def test_changed_digital_waveform_is_not_hidden_by_matching_psg_writes(self):
        p = self.root / "dut/audio.u8"
        data = bytearray(p.read_bytes())
        data[129] = 63
        p.write_bytes(data)
        result = compare(self.root, self.scenario)
        self.assertFalse(result["passed"])
        self.assertTrue(result["sound_events"]["passed"])
        self.assertEqual(result["audio"]["digital_waveform"]["different_samples"], 1)
        self.assertEqual(result["audio"]["digital_waveform"]["first_difference_sample"], 129)

    def test_missing_or_truncated_digital_capture_cannot_pass(self):
        p = self.root / "reference/audio-digital.u8"
        p.write_bytes(bytes(24999))
        with self.assertRaises(ValueError):
            compare(self.root, self.scenario)
        p.unlink()
        with self.assertRaises(OSError):
            compare(self.root, self.scenario)

    def test_changed_disc_sector_and_missing_save_fail(self):
        p = self.root / "dut/end.ssd"
        data = bytearray(p.read_bytes())
        data[1024] = 1
        p.write_bytes(data)
        self.assertFalse(compare(self.root, self.scenario)["disc"]["passed"])
        data[1024] = 0
        p.write_bytes(data)
        self.scenario["disc_assertions"] = [{"checkpoint": "end", "file": "E.TEST", "size": 256}]
        self.assertFalse(compare(self.root, self.scenario)["disc"]["passed"])

    def test_missing_disc_and_changed_geometry_fail(self):
        p = self.root / "dut/end.ssd"
        data = bytearray(p.read_bytes())
        data[263] = 31
        p.write_bytes(data)
        with self.assertRaises(ValueError):
            compare(self.root, self.scenario)
        p.unlink()
        with self.assertRaises(OSError):
            compare(self.root, self.scenario)

    def test_energy_and_commander_mutations_fail(self):
        symbols = reference_symbols()
        p = self.root / "dut/end.ram"
        data = bytearray(p.read_bytes())
        data[symbols["ENERGY"]] = 1
        p.write_bytes(data)
        result = compare(self.root, self.scenario)
        self.assertFalse(result["passed"])
        self.assertEqual(result["checkpoints"][1]["state_differences"][0]["field"], "energy")

    def test_one_changed_colour_pixel_fails(self):
        p = self.root / "dut/end.ppm"
        pixels = np.zeros((248, 256, 3), np.uint8)
        pixels[192, 212] = [255, 255, 0]
        Image.fromarray(pixels).save(p)
        result = compare(self.root, self.scenario)
        self.assertFalse(result["passed"])
        self.assertEqual(result["checkpoints"][1]["video"]["different_pixels"], 1)

    def test_one_changed_sound_timestamp_fails(self):
        (self.root / "reference/psg.tsv").write_text("2\t144\n")
        (self.root / "dut/psg.tsv").write_text("33\t144\n")
        self.assertFalse(compare(self.root, self.scenario)["sound_events"]["passed"])

    def test_missing_or_truncated_captures_fail(self):
        (self.root / "dut/end.ram").write_bytes(bytes(32768))
        with self.assertRaises(ValueError):
            compare(self.root, self.scenario)

    def test_missing_matrix_transition_fails(self):
        (self.root / "dut/inputs.tsv").write_text("320000 6 7 1\n")
        self.assertFalse(compare(self.root, self.scenario)["inputs"]["passed"])

    def test_wrong_matrix_position_or_time_fails(self):
        for capture in ["320000 5 7 1\n640000 6 7 0\n", "320001 6 7 1\n640000 6 7 0\n"]:
            (self.root / "dut/inputs.tsv").write_text(capture)
            self.assertFalse(compare(self.root, self.scenario)["inputs"]["passed"])

    def test_wrong_reference_key_or_late_delivery_fails(self):
        path = self.root / "reference/inputs.json"
        events = json.loads(path.read_text())
        events[0]["keys"] = ["F0"]
        write_json(path, events)
        self.assertFalse(compare(self.root, self.scenario)["inputs"]["passed"])
        events[0]["keys"] = ["F8"]
        events[0]["cycle"] += 33
        write_json(path, events)
        self.assertFalse(compare(self.root, self.scenario)["inputs"]["passed"])

    def test_duplicate_or_dropped_frame_fails(self):
        for capture in ["640000\n640000\n", "640000\n"]:
            (self.root / "dut/frames.tsv").write_text(capture)
            with self.assertRaises(ValueError):
                compare(self.root, self.scenario)

    def test_completed_field_at_exact_endpoint_is_valid(self):
        # Recorders timestamp an edge after its final executed master cycle.
        rs = {"frames": 2, "duration_cycles": 100000}
        ds = {"frames": 2, "duration_cycles": 1600000}
        (self.root / "reference/frames.tsv").write_text("40000\n100000\n")
        (self.root / "dut/frames.tsv").write_text("640000\n1600000\n")
        self.assertTrue(captured_frames(self.root, rs, ds)["passed"])
        for bad in ("0\n1600000\n", "640000\n1600001\n"):
            (self.root / "dut/frames.tsv").write_text(bad)
            with self.assertRaises(ValueError):
                captured_frames(self.root, rs, ds)

    def test_one_master_cycle_frame_offset_fails(self):
        (self.root / "dut/frames.tsv").write_text("640001\n1280000\n")
        result = compare(self.root, self.scenario)
        self.assertFalse(result["passed"])
        self.assertEqual(result["cadence"]["first_difference_master_cycles"], 1)

    def test_checkpoint_timing_mutation_fails(self):
        path = self.root / "reference/summary.json"
        summary = json.loads(path.read_text())
        summary["checkpoints"][1]["cycle"] += 33
        write_json(path, summary)
        with self.assertRaises(ValueError):
            compare(self.root, self.scenario)

    def test_reference_snapshot_never_reads_guest_mmio(self):
        module = (ROOT / "tools/validation/reference_memory.mjs").as_uri()
        script = f"import {{memorySnapshot}} from {json.dumps(module)};let calls=0;const cpu={{peekmem:a=>{{calls++;return a&255}},readmem:()=>{{throw Error('MMIO was read')}}}};const b=memorySnapshot(cpu);if(calls!==65536 || b.length!==65536 || b[0xfe84]!==0x84)throw Error('Bad snapshot');"
        subprocess.run(
            ["node", "--input-type=module", "-e", script], check=True, capture_output=True
        )

    def test_video_observer_records_edges_inside_cpu_batches(self):
        module = (ROOT / "tools/validation/reference_timing.mjs").as_uri()
        script = f"""import {{observeVideoClock}} from {json.dumps(module)};
let ticks=0;const edges=[];let clock;
const video={{polltime:n=>{{for(let i=0;i<n;i++){{ticks++;if(ticks%4===0)edges.push(clock.cycle);}}}}}};
clock=observeVideoClock(video,100);video.polltime(3);video.polltime(6);
if(ticks!==9 || clock.cycle!==109 || JSON.stringify(edges)!=='[104,108]')throw Error('Edges were rounded to batch end');
clock.reset(200);video.polltime(3);if(clock.cycle!==203 || edges.at(-1)!==203)throw Error('Restore clock failed');
for(const n of [-1,1.5]){{let rejected=false;try{{video.polltime(n)}}catch{{rejected=true}}if(!rejected)throw Error('Invalid batch accepted');}}
"""
        subprocess.run(
            ["node", "--input-type=module", "-e", script], check=True, capture_output=True
        )

    def test_keyboard_stimuli_arrive_inside_instruction_batches(self):
        module = (ROOT / "tools/bbc/reference_inputs.mjs").as_uri()
        from tools.validation.common import source_root

        scheduler = (source_root("jsbeeb") / "src/scheduler.js").as_uri()
        script = f"""import {{scheduleKeys}} from {json.dumps(module)};
import {{Scheduler}} from {json.dumps(scheduler)};
const scheduler=new Scheduler();scheduler.polltime(100);
const transitions=[];const session={{keyDownRaw:k=>transitions.push(['down',...k,scheduler.epoch]),keyUpRaw:k=>transitions.push(['up',...k,scheduler.epoch])}};
const events=[{{at_us:0,keys:[]}},{{at_us:10,keys:['A']}},{{at_us:15,keys:['A','B']}},{{at_us:20,keys:['B']}},{{at_us:25,keys:[]}}];
const captures=scheduleKeys(scheduler,session,{{A:[1,2],B:[3,4]}},events);
scheduler.polltime(23);scheduler.polltime(31);
if(JSON.stringify(captures.map(c=>c.cycle))!=='[0,20,30,40,50]')throw Error('Inputs rounded to instruction boundaries');
if(JSON.stringify(transitions)!=='[["down",1,2,120],["down",3,4,130],["up",1,2,140],["up",3,4,150]]')throw Error('Held key was released or transition clock changed');
for(const event of [{{at_us:-1,keys:[]}},{{at_us:.5,keys:[]}},{{at_us:1,keys:['UNKNOWN']}},{{at_us:1,keys:['A','A']}},{{at_us:1,keys:['constructor']}}]){{
 let failed=false;try{{scheduleKeys(scheduler,session,{{A:[1,2]}},[event])}}catch{{failed=true}}if(!failed)throw Error('Invalid stimulus accepted');
}}
"""
        subprocess.run(
            ["node", "--input-type=module", "-e", script], check=True, capture_output=True
        )

    def test_reference_input_one_cycle_late_fails(self):
        path = self.root / "reference/inputs.json"
        events = json.loads(path.read_text())
        events[0]["cycle"] += 1
        write_json(path, events)
        self.assertFalse(compare(self.root, self.scenario)["inputs"]["passed"])

    def test_changed_or_missing_analogue_event_fails(self):
        self.scenario["analogue"] = [
            {"at_us": 10000, "channels": [16384, 49152, 32768, 32768], "buttons": 1}
        ]
        path = self.root / "dut/summary.json"
        summary = json.loads(path.read_text())
        summary["analogue_inputs"] = 1
        write_json(path, summary)
        capture = self.root / "dut/analogue.tsv"
        capture.write_text("320000 16384 49152 32768 32768 1\n")
        write_json(
            self.root / "reference/analogue.json",
            [
                {
                    "requested_cycle": 20000,
                    "cycle": 20000,
                    "channels": [16384, 49152, 32768, 32768],
                    "buttons": 1,
                }
            ],
        )
        self.assertTrue(compare(self.root, self.scenario)["analogue_inputs"]["passed"])
        for row in [
            "320001 16384 49152 32768 32768 1\n",
            "320000 16385 49152 32768 32768 1\n",
            "320000 16384 49152 32768 32768 0\n",
            "",
        ]:
            capture.write_text(row)
            self.assertFalse(compare(self.root, self.scenario)["analogue_inputs"]["passed"])
        capture.unlink()
        with self.assertRaises(OSError):
            compare(self.root, self.scenario)

    def test_invalid_analogue_stimuli_are_rejected(self):
        from tools.bbc.inputs import analogue_transitions

        valid = {"at_us": 0, "channels": [32768] * 4, "buttons": 0}
        for event in [
            {**valid, "at_us": True},
            {**valid, "at_us": -1},
            {**valid, "at_us": 50000},
            {**valid, "channels": [0] * 3},
            {**valid, "channels": [65536] * 4},
            {**valid, "channels": [True] * 4},
            {**valid, "buttons": 4},
        ]:
            with self.assertRaises(ValueError):
                analogue_transitions({**self.scenario, "analogue": [event]})

    def test_reference_analogue_controls_arrive_at_exact_pin_clock(self):
        from tools.validation.common import source_root

        module = (ROOT / "tools/bbc/reference_analogue.mjs").as_uri()
        scheduler = (source_root("jsbeeb") / "src/scheduler.js").as_uri()
        script = f"""import assert from 'node:assert/strict';
import {{scheduleAnalogue}} from {json.dumps(module)};
import {{Scheduler}} from {json.dumps(scheduler)};
const scheduler=new Scheduler();scheduler.polltime(100);
const sources=[],buttons=[];
const cpu={{adconverter:{{setChannelSource:(c,s)=>sources[c]=s}},sysvia:{{setJoystickButton:(b,v)=>buttons[b]=v}}}};
const events=[{{at_us:0,channels:[32768,32768,32768,32768],buttons:0}},{{at_us:10,channels:[0,65535,16384,49152],buttons:3}},{{at_us:25,channels:[32768,32768,32768,32768],buttons:0}}];
const captures=scheduleAnalogue(scheduler,cpu,events,30);
scheduler.polltime(23);
assert.deepEqual(captures.map(c=>c.cycle),[0,20]);
assert.deepEqual(sources.map(s=>s.getValue()),events[1].channels);
assert.deepEqual(buttons,[true,true]);
scheduler.polltime(31);
assert.deepEqual(captures.map(c=>c.cycle),[0,20,50]);
assert.deepEqual(buttons,[false,false]);
for(const event of [{{...events[0],at_us:-1}},{{...events[0],buttons:4}},{{...events[0],channels:[0]}}])assert.throws(()=>scheduleAnalogue(scheduler,cpu,[event],30));
"""
        subprocess.run(
            ["node", "--input-type=module", "-e", script], check=True, capture_output=True
        )
