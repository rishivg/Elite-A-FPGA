"""Pair recovery requires exact inputs, own boots and both archived captures."""

import tempfile
import unittest
from pathlib import Path
from threading import Barrier
from unittest.mock import patch

from tools.bbc import pair_reuse
from tools.validation.common import digest, write_json


class PairReuseTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.bundle = Path(temporary.name)
        self.workspace = self.bundle / "workspace"
        self.source = self.workspace / "build/bbc/runs/old"
        self.source.mkdir(parents=True)
        self.output = self.source.parent / "new"
        self.output.mkdir()
        self.fixture = self.workspace / "fixture"
        self.fixture.write_bytes(b"fixture")
        self.manifest = {
            "target": "Elite-A",
            "master_clock_hz": 96000000,
            "clock_hz": 32000000,
            "repetitions": 2,
            "capture_storage": "verified-gzip",
            "trace_bus": True,
            "compiler_profile": "qualified-native",
            "inputs": {"fixture": digest(self.fixture)},
            "reference_observations_source": None,
            "reference_options": {"reference_cpu": "cycle"},
            "scenarios": ["recorded", "running"],
        }
        write_json(self.source / "manifest.json", self.manifest)
        backends, hashes = {}, {}
        for name in ("dut", "reference"):
            state = self.workspace / f"{name}.state"
            state.write_text(name)
            value = digest(state)
            backends[name] = {
                "independent_execution": True,
                "inputs": self.manifest["inputs"],
                "states": {state.name: value},
            }
            hashes[str(state)] = value
        self.snapshots = {
            "released": {"independent_boot_receipt": {"backends": backends}, "hashes": hashes}
        }
        write_json(self.source / "boot-snapshots.json", {"media_profiles": self.snapshots})
        self.scenario = {"name": "recorded"}
        for repetition in range(2):
            case, _ = self.prepare(self.scenario, repetition, self.source)
            for name in ("reference", "dut"):
                (case / name).mkdir()
                (case / name / "screen").write_text("screen")
            write_json(
                case / "comparison.json",
                {
                    **self.scenario,
                    "passed": True,
                    "artifact_hashes": pair_reuse.capture_hashes(case),
                },
            )

    @staticmethod
    def prepare(scenario, repetition, root):
        case = root / (scenario["name"] + ("-repeat" if repetition else ""))
        case.mkdir()
        write_json(case / "scenario.json", scenario)
        return case, {}

    def test_published_repetition_is_available_before_a_pair_finishes(self):
        source, manifest = pair_reuse.verify_source(self.source, self.manifest)
        first_receipt = source / "recorded/comparison.json"
        first_receipt.write_text("{")  # The first repetition was interrupted during publication.
        self.assertEqual(pair_reuse.completed(source, manifest, self.snapshots), {"recorded": (1,)})

    def test_previously_failed_pair_is_replayed(self):
        source, manifest = pair_reuse.verify_source(self.source, self.manifest)
        write_json(
            source / "progress.json",
            {"results": [{**self.scenario, "passed": False, "repeatable": False}]},
        )
        self.assertEqual(pair_reuse.completed(source, manifest, self.snapshots), {})

    def test_verified_boots_bind_the_completed_pair(self):
        source, manifest = pair_reuse.verify_source(self.source, self.manifest)
        write_json(
            source / "progress.json",
            {
                "results": [
                    {
                        **self.scenario,
                        "passed": True,
                        "repeatable": True,
                        "repetitions": [{"passed": True}] * 2,
                    }
                ]
            },
        )
        self.assertEqual(
            pair_reuse.completed(source, manifest, self.snapshots), {"recorded": (0, 1)}
        )
        (self.workspace / "reference.state").write_text("changed")
        with self.assertRaisesRegex(ValueError, "same verified own-machine boots"):
            pair_reuse.completed(source, manifest, self.snapshots)

    def test_stale_inputs_or_capture_options_fail_before_boot_checks(self):
        with self.assertRaisesRegex(ValueError, "identical replay inputs"):
            pair_reuse.verify_source(self.source, self.manifest | {"trace_bus": False})
        self.fixture.write_bytes(b"changed after capture")
        with self.assertRaisesRegex(ValueError, "inputs have changed"):
            pair_reuse.verify_source(self.source, self.manifest)

    def test_only_missing_media_boots_are_restored_from_the_interrupted_bundle(self):
        write_json(self.bundle / "execution-bundle.json", {"independent_file_copies": True})
        medium = object()
        options = self.manifest["reference_options"]
        with (
            patch.object(pair_reuse.boot_cache, "valid", side_effect=(True, False)) as valid,
            patch.object(pair_reuse.boot_cache, "clock_multiplier", return_value=3),
            patch.object(pair_reuse.boot_reuse, "reuse") as reuse,
        ):
            pair_reuse.recover_boots(self.source, {None: None, "extra": medium}, options)
        self.assertEqual(valid.call_count, 2)
        reuse.assert_called_once_with(
            self.bundle,
            sealed_boots_only=True,
            run=Path("build/bbc/runs/old"),
            clock_multiplier=3,
            context=medium,
            reference_options=options,
        )

    def test_copy_compares_once_and_publishes_independent_outputs(self):
        with patch.object(
            pair_reuse, "compare", return_value={**self.scenario, "passed": True}
        ) as check:
            recovered = pair_reuse.copy_captures(
                self.source, self.output, self.scenario, (0, 1), self.prepare
            )
        first, second = recovered.values()
        check.assert_called_once()
        self.assertEqual(first, second)
        for name in ("recorded", "recorded-repeat"):
            self.assertEqual(
                pair_reuse.capture_hashes(self.output / name), first["artifact_hashes"]
            )
            self.assertFalse(
                (self.output / name / "dut/screen").samefile(self.source / name / "dut/screen")
            )

    def test_copy_second_repetition_alone_retains_its_identity(self):
        with patch.object(pair_reuse, "compare", return_value={**self.scenario, "passed": True}):
            recovered = pair_reuse.copy_captures(
                self.source, self.output, self.scenario, (1,), self.prepare
            )
        self.assertEqual(list(recovered), [("recorded", 1)])
        self.assertEqual([path.name for path in self.output.iterdir()], ["recorded-repeat"])
        reuse = pair_reuse.read(self.output / "recorded-repeat/pair-reuse.json")
        self.assertEqual(reuse["source_case"], str(self.source / "recorded-repeat"))

    def test_mutated_repeat_leaves_no_partial_copy(self):
        (self.source / "recorded-repeat/dut/screen").write_text("different")
        with (
            patch.object(pair_reuse, "compare") as check,
            self.assertRaisesRegex(ValueError, "Copied captures changed"),
        ):
            pair_reuse.copy_captures(self.source, self.output, self.scenario, (0, 1), self.prepare)
        check.assert_not_called()
        self.assertEqual(list(self.output.iterdir()), [])

    def test_malformed_receipt_leaves_no_partial_copy(self):
        write_json(self.source / "recorded-repeat/comparison.json", None)
        with self.assertRaisesRegex(ValueError, "Missing passing comparison"):
            pair_reuse.copy_captures(self.source, self.output, self.scenario, (0, 1), self.prepare)
        self.assertEqual(list(self.output.iterdir()), [])

    def test_parallel_recovery_keeps_successful_peer_when_an_archive_fails(self):
        scenarios = [self.scenario | {"duration_us": 10}, {"name": "damaged", "duration_us": 20}]
        overlap = Barrier(2)
        result = {("recorded", 0): {"passed": True}}

        def copy(source, output, scenario, repetitions, prepare):
            overlap.wait(timeout=5)
            if scenario["name"] == "damaged":
                raise ValueError("bad archive")
            return result

        with patch.object(pair_reuse, "copy_captures", side_effect=copy):
            recovered = pair_reuse.recover(
                self.source,
                self.output,
                scenarios,
                {"recorded": (0,), "damaged": (0, 1)},
                self.prepare,
                2,
            )
        self.assertEqual(recovered, result)
