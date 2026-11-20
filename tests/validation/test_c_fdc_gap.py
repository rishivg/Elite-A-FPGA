"""Reject missing R21 evidence, wrong source deadlines and incomplete state."""

import copy
import json
import tempfile
import unittest
from pathlib import Path

from tools.bbc.proofs.cpu_reference import fdc_gap as f


class GapQualificationTests(unittest.TestCase):
    def body(self):
        state = list(range(1000, 1000 + f.WIDTH))
        nets = {}
        offset = 0
        for name, width in f.FIELDS.items():
            nets[name] = {"bits": state[offset : offset + width]}
            offset += width
        return {
            "ports": {"proofState": {"bits": state}, "proofNext": {"bits": state}},
            "netnames": nets,
        }

    def cases(self):
        return [
            dict(
                name=f"gap-{kind}-{value}-{offset}",
                value=value,
                offset=offset,
                gap=1203617,
                nominal=1206049,
                first=1242529,
                rows=38,
            )
            for kind, values, offsets in (
                ("register", (0, 1, 11, 255), (-1, 0, 1, 127, 128, 129)),
                ("command", (16, 28, 52), (0, 1, 127, 128, 129)),
            )
            for value in values
            for offset in offsets
        ]

    def fixture(self, c):
        start = c["gap"] + c["offset"] - 2
        target = 21 if c["name"].startswith("gap-register-") else 31
        first = c["first"]
        rows = (
            [(start, "W", 0, 0x7A), (start + 1, "W", 1, target), (start + 2, "W", 1, c["value"])]
            + [(first + d, "P", 0, 0) for d in (-1, 0, 1, 127, 128, 129, 1024)]
            + [(first + 1025, "R", 1, 0), (first + 2048, "P", 0, 0), (first + 2049, "W", 2, 1)]
        )
        return (
            "case 0 "
            + c["name"]
            + " "
            + str(len(rows))
            + "\n"
            + "\n".join(" ".join(map(str, (*r, 0, 0, 0))) for r in rows)
            + "\n"
        )

    def test_complete_unique_controller_state_is_required(self):
        f.indices(self.body())
        self.assertEqual(f.WIDTH, 934)
        for fault in ("missing", "duplicate", "alias", "foreign", "narrow"):
            b = self.body()
            if fault == "missing":
                b["ports"]["proofState"]["bits"].pop()
            elif fault == "duplicate":
                b["ports"]["proofState"]["bits"][-1] = 1000
            elif fault == "alias":
                b["netnames"]["driveInCopy"] = b["netnames"]["driveInLatched"]
            elif fault == "foreign":
                b["netnames"]["dataSyncZeros"]["bits"][0] = 99999
            else:
                b["ports"]["proofNext"]["bits"].pop()
            with self.subTest(fault=fault), self.assertRaises(ValueError):
                f.indices(b)

    def test_each_input_and_original_irq_deadline_is_required(self):
        cases = self.cases()
        self.assertEqual(len(cases), 39)
        self.assertEqual(sum(c["rows"] for c in cases), 1482)
        self.assertTrue({v[0] for v in f.FAULTS.values()}.issubset(f.NAMES))
        with tempfile.TemporaryDirectory() as directory:
            p = Path(directory) / "original.json"
            p.write_text(json.dumps({"passed": True, "cases": cases}))
            f.original(p)
            for fault in (
                "missing",
                "reorder",
                "value",
                "offset",
                "first",
                "gap",
                "nominal",
                "rows",
                "verdict",
            ):
                r = {"passed": True, "cases": copy.deepcopy(cases)}
                if fault == "missing":
                    r["cases"].pop()
                elif fault == "reorder":
                    r["cases"].reverse()
                elif fault == "verdict":
                    r["passed"] = False
                else:
                    r["cases"][0][fault] = {
                        "value": True,
                        "offset": True,
                        "first": 1203617,
                        "gap": 0,
                        "nominal": 1203617,
                        "rows": 37,
                    }[fault]
                p.write_text(json.dumps(r))
                with self.subTest(fault=fault), self.assertRaises(ValueError):
                    f.original(p)

    def test_exact_programming_and_passive_pre_irq_probes_are_required(self):
        cases = self.cases()
        selected = {c["name"]: self.fixture(c) for c in cases}
        f.timelines(cases, selected)
        for a, b in [
            ("W 1 21", "W 1 20"),
            ("W 1 31", "W 1 30"),
            ("1242528 P 0", "1242528 R 0"),
            ("1242530 P 0", "1242530 R 0"),
            ("1243554 R 1", "1243554 P 1"),
            ("1244578 W 2 1", "1244578 W 2 0"),
        ]:
            altered = {n: s.replace(a, b) for n, s in selected.items()}
            self.assertNotEqual(altered, selected)
            with self.subTest(missing=a), self.assertRaises(ValueError):
                f.timelines(cases, altered)

    def test_faults_require_all_current_targets(self):
        predicate = "  assign when_Intel8271_l1232 = (dataSyncZeros == 2'b11);\n  assign when_Intel8271_l1293 = (dataSyncZeros == 2'b11);"
        self.assertNotEqual(f.fault(predicate, "gap-gate"), predicate)
        with self.assertRaises(ValueError):
            f.fault(predicate + "\n" + predicate, "gap-gate")
        for name, text in (
            ("register", "hostDriveInCopy = startParameters_1;\neventDriveInCopy = specialData;"),
            ("sample", "hostDriveInCopy = driveIn;"),
        ):
            self.assertNotEqual(f.fault(text, name), text)
            for bad in ("", text + text):
                with self.subTest(fault=name), self.assertRaises(ValueError):
                    f.fault(bad, name)
        for name, (a, b) in f.KERNEL_FAULTS.items():
            self.assertEqual(f.kernel_fault(a, name), b)
            with self.assertRaises(ValueError):
                f.kernel_fault(a + a, name)


if __name__ == "__main__":
    unittest.main()
