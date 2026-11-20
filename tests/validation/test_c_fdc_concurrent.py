"""Reject missing overlap cases, incomplete state and false negative controls."""

import copy
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from tools.bbc.proofs.cpu_reference import fdc_concurrent as f


class ConcurrentFdcTests(unittest.TestCase):
    def body(self):
        state = list(range(1000, 1000 + f.WIDTH))
        names, offset = {}, 0
        for name, width in f.FIELDS.items():
            names[name] = {"bits": state[offset : offset + width]}
            offset += width
        return {
            "ports": {"proofState": {"bits": state}, "proofNext": {"bits": state}},
            "netnames": names,
        }

    def test_full_state_inventory_rejects_aliases_and_omissions(self):
        f.indices(self.body())
        for fault in ("missing", "duplicate", "alias", "foreign", "narrow"):
            body = copy.deepcopy(self.body())
            if fault == "missing":
                body["ports"]["proofState"]["bits"].pop()
            elif fault == "duplicate":
                body["ports"]["proofState"]["bits"][-1] = 1000
            elif fault == "alias":
                body["netnames"]["mechanicalOp"] = body["netnames"]["executingOp"]
            elif fault == "foreign":
                body["netnames"]["mechanicalOp"]["bits"][0] = 99999
            else:
                body["netnames"]["mechanicalOp"]["bits"].pop()
            with self.subTest(fault=fault), self.assertRaises(ValueError):
                f.indices(body)

    def test_overlap_inventory_includes_no_step_and_all_deadline_offsets(self):
        self.assertEqual(len(f.NAMES), 252)
        self.assertEqual(len(set(f.NAMES)), 252)
        self.assertTrue({n for n, _ in f.FAULTS.values()}.issubset(f.NAMES))
        cases = []
        for name in f.NAMES:
            # A negative offset is represented by the trailing double dash.
            parts = name.removeprefix("overlap-").split("-", 5)
            old, new, track, sector, ack, offset = map(int, parts)
            cases.append(
                dict(
                    name=name,
                    old=old,
                    next=new,
                    track=track,
                    sector=sector,
                    ack=bool(ack),
                    offset=offset,
                    first=900000,
                    deadline=900128,
                    rows=30,
                )
            )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "original.json"
            report = dict(passed=True, cases=cases)
            path.write_text(json.dumps(report))
            f.original(path)
            for fault in ("missing", "reordered", "rows", "deadline", "track", "verdict"):
                changed = copy.deepcopy(report)
                if fault == "missing":
                    changed["cases"].pop()
                elif fault == "reordered":
                    changed["cases"].reverse()
                elif fault == "verdict":
                    changed["passed"] = False
                elif fault == "rows":
                    changed["cases"][0]["rows"] = 0
                elif fault == "track":
                    changed["cases"][0]["track"] = 2
                else:
                    changed["cases"][0]["deadline"] += 1
                path.write_text(json.dumps(changed))
                with self.subTest(fault=fault), self.assertRaises(ValueError):
                    f.original(path)

    def test_staging_a_fixture_fault_never_writes_through_a_symlink(self):
        with tempfile.TemporaryDirectory() as directory:
            source, staged = Path(directory) / "source", Path(directory) / "staged"
            source.mkdir()
            staged.mkdir()
            files = {"first.bin": b"first", "last.bin": b"last"}
            for name, data in files.items():
                (source / name).write_bytes(data)

            f.stage_fixture_fault(source, staged, files, "last.bin")
            self.assertEqual((staged / "first.bin").readlink(), source / "first.bin")
            self.assertEqual((staged / "last.bin").read_bytes(), b"lasu")

            f.stage_fixture_fault(source, staged, files, "first.bin")
            self.assertEqual((staged / "first.bin").read_bytes(), b"firsu")
            self.assertEqual((staged / "last.bin").readlink(), source / "last.bin")
            self.assertEqual({name: (source / name).read_bytes() for name in files}, files)

    def test_missing_fault_targets_fail_closed(self):
        for name in f.FAULTS:
            with self.subTest(name=name), self.assertRaises(ValueError):
                f.fault("", name)
        for indent in (16, 18):
            source = (
                "mechanicalDelay <= (stepDelay - 24'h000011);\n"
                + " " * indent
                + "mechanicalPhase <= Mechanical_Seek;"
            )
            self.assertIn("phase_1 <= Phase_Idle;", f.fault(source, "stopped-byte"))
            with self.assertRaises(ValueError):
                f.fault(source + "\n" + source, "stopped-byte")

    def test_mechanical_fault_requires_all_completion_cancellation_sites(self):
        site = "mechanicalPhase <= Mechanical_Idle;\n dispatchPending <= 1'b0;\n"
        self.assertEqual(
            f.fault(site * 37, "uncancelled-mechanics").count("dropped mechanical cancellation"),
            37,
        )
        for count in (36, 38):
            with self.subTest(count=count), self.assertRaises(ValueError):
                f.fault(site * count, "uncancelled-mechanics")

    def test_sync_premise_requires_every_property_and_fault(self):
        checks = [
            {"name": name, "accepted": True}
            for name in (
                "kernel-positive",
                "kernel-wrong-byte",
                "kernel-early-plateau",
                "last-oracle-record",
            )
        ]
        for multiplier in (1, 3):
            checks.extend(
                {
                    "name": "positive",
                    "property": p,
                    "clock_multiplier": multiplier,
                    "accepted": True,
                }
                for p in ("prefixEquivalent", "ageBound", "checkingBound", "countBound")
            )
            checks.append(
                {
                    "name": "wrong-delimited-byte",
                    "property": "prefixEquivalent",
                    "clock_multiplier": multiplier,
                    "accepted": True,
                }
            )
            checks.extend(
                {"name": name, "clock_multiplier": multiplier, "accepted": True}
                for name in ("native-positive", "native-wrong-delimited-byte")
            )
        report = {"passed": True, "register_bits": 934, "checks": checks}
        f.sync_premise(report)
        for fault in ("missing", "duplicate", "rejected", "clock", "width", "verdict"):
            changed = copy.deepcopy(report)
            if fault == "missing":
                changed["checks"].pop()
            elif fault == "duplicate":
                changed["checks"][-1] = changed["checks"][-2]
            elif fault == "rejected":
                changed["checks"][-1]["accepted"] = False
            elif fault == "clock":
                changed["checks"][-1]["clock_multiplier"] = 2
            elif fault == "width":
                changed["register_bits"] = 933
            else:
                changed["passed"] = False
            with self.subTest(fault=fault), self.assertRaises(ValueError):
                f.sync_premise(changed)

    def test_media_fault_requires_named_bounded_difference(self):
        name = f.NAMES[-1]
        row = f"FDC_MEDIA_MISMATCH {name} cell 249999 value 65535/65534\n"
        self.assertTrue(f.media_mismatch(subprocess.CompletedProcess([], 1, row, ""), name))
        for code, out, err in (
            (0, row, ""),
            (2, row, ""),
            (1, row, "timeout"),
            (1, row.replace("65534", "65535"), ""),
            (1, row.replace("249999", "250000"), ""),
            (1, row.replace("65534", "65536"), ""),
            (1, "Missing media\n", ""),
        ):
            with self.subTest(code=code, out=out, err=err):
                self.assertFalse(
                    f.media_mismatch(subprocess.CompletedProcess([], code, out, err), name)
                )


if __name__ == "__main__":
    unittest.main()
