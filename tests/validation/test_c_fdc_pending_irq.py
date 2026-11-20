"""Reject missing R21 evidence, wrong source deadlines and incomplete state."""

import copy
import json
import tempfile
import unittest
from pathlib import Path

from tools.bbc.proofs.cpu_reference import fdc_pending_irq as f


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
                name=f"pending-irq-{kind}-{value}-{offset}",
                kind=kind,
                value=value,
                offset=offset,
                entry=1203617,
                first=1242529,
                rows=39,
            )
            for kind in ("read", "write", "run", "marker")
            for value in (1, 48, 49, 112, 113)
            for offset in (126, 128, 130)
        ]

    def fixture(self, c):
        start = c["entry"] + c["offset"] - 2
        first = c["first"]
        rows = (
            [(start, "W", 0, 0x7A), (start + 1, "W", 1, 23), (start + 2, "W", 1, c["value"])]
            + [(first + d, "P", 0, 0) for d in (-1, 0, 1, 127, 128, 129, 1024, 2048)]
            + [(first + 2, "R", 4, 0), (first + 1025, "R", 1, 0), (first + 2049, "W", 2, 1)]
        )
        rows.sort()
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
                b["netnames"]["mode"] = b["netnames"]["data"]
            elif fault == "foreign":
                b["netnames"]["dataSyncZeros"]["bits"][0] = 99999
            else:
                b["ports"]["proofNext"]["bits"].pop()
            with self.subTest(fault=fault), self.assertRaises(ValueError):
                f.indices(b)

    def test_each_input_and_original_irq_deadline_is_required(self):
        cases = self.cases()
        self.assertEqual(len(cases), 60)
        self.assertEqual(sum(c["rows"] for c in cases), 2340)
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
                "entry",
                "rows",
                "verdict",
                "kind",
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
                        "first": 1203617 + 126 + 1,
                        "entry": 0,
                        "rows": 38,
                        "kind": "other",
                    }[fault]
                p.write_text(json.dumps(r))
                with self.subTest(fault=fault), self.assertRaises(ValueError):
                    f.original(p)

    def test_exact_programming_and_passive_pre_irq_probes_are_required(self):
        cases = self.cases()
        selected = {c["name"]: self.fixture(c) for c in cases}
        f.timelines(cases, selected)
        for a, b in [
            ("W 1 23", "W 1 22"),
            ("1242528 P 0", "1242528 R 0"),
            ("1242531 R 4", "1242531 P 4"),
            ("1243554 R 1", "1243554 P 1"),
            ("1244578 W 2 1", "1244578 W 2 0"),
        ]:
            altered = {n: s.replace(a, b) for n, s in selected.items()}
            self.assertNotEqual(altered, selected)
            with self.subTest(missing=a), self.assertRaises(ValueError):
                f.timelines(cases, altered)

    def test_faults_require_all_current_targets(self):
        samples = {
            "off": "  assign gapByte = someExpression;",
            "gating": "\n".join(
                "if(gapByte) begin if(byteIrqs) begin dummy=1; end end" for _ in range(4)
            ),
            "data": "if(gapByte) begin eventData = io_storageData[7 : 0]; end",
            "result": "if(gapByte) begin eventResult = (_zz_eventResult_28 | result); end",
        }
        for name, text in samples.items():
            self.assertNotEqual(f.fault(text, name), text)
            for bad in ("", text + "\n" + text):
                with self.subTest(fault=name), self.assertRaises(ValueError):
                    f.fault(bad, name)
        with self.assertRaises(ValueError):
            f.blocks("if(gapByte) begin")
        for name, (a, b) in f.KERNEL_FAULTS.items():
            self.assertEqual(f.kernel_fault(a, name), b)
            with self.assertRaises(ValueError):
                f.kernel_fault(a + a, name)


if __name__ == "__main__":
    unittest.main()
