"""Reject narrowed completion state and incomplete source witness histories."""

import copy
import json
import tempfile
import unittest
from pathlib import Path

from tools.bbc.proofs.cpu_reference import fdc_completion as f


class CompletionQualificationTests(unittest.TestCase):
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
                name=f"completion-specify-{count}-{offset}-{observation}",
                count=count,
                offset=offset,
                observation=observation,
                deadline=1238945,
                rows=296 if observation == "register" else (300 if offset == -1 else 298),
            )
            for count in (2, 34, 66)
            for offset in (-1, 0, 1)
            for observation in ("register", "continuation")
        ]

    def fixture(self, c):
        d = c["deadline"]
        rows = [
            (20, "W", 0, 0x75),
            (21, "W", 1, 5),
            (22, "W", 1, c["count"]),
            (d + c["offset"], "W", 1, 0x17),
            (d + 2, "R", 3, 0),
            (d + 3, "R", 2, 0),
        ]
        rows += [(d + n, "P", 0, 0) for n in (127, 128, 129, 1024, 40000)]
        if c["observation"] == "register":
            rows += [(d + 40001, "W", 0, 0x7D), (d + 40002, "W", 1, 6), (d + 40003, "R", 1, 1)]
        else:
            for i in range(3 if c["offset"] < 0 else 2):
                rows += [
                    (d + 40001 + 2 * i, "W", 1, 0x37 + 0x10 * i),
                    (d + 40002 + 2 * i, "P", 0, 0),
                ]
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
        for fault in ("missing", "duplicate", "alias", "foreign", "narrow"):
            b = self.body()
            if fault == "missing":
                b["ports"]["proofState"]["bits"].pop()
            elif fault == "duplicate":
                b["ports"]["proofState"]["bits"][-1] = 1000
            elif fault == "alias":
                b["netnames"]["parameters_0"] = b["netnames"]["parameters_1"]
            elif fault == "foreign":
                b["netnames"]["parameterCount"]["bits"][0] = 99999
            else:
                b["ports"]["proofNext"]["bits"].pop()
            with self.subTest(fault=fault), self.assertRaises(ValueError):
                f.indices(b)

    def test_all_histories_coordinates_and_counts_are_required(self):
        cases = self.cases()
        self.assertEqual(len(cases), 18)
        self.assertEqual(sum(c["rows"] for c in cases), 5352)
        self.assertTrue({v[0] for v in f.FAULTS.values()}.issubset(f.NAMES))
        with tempfile.TemporaryDirectory() as directory:
            p = Path(directory) / "original.json"
            p.write_text(json.dumps({"passed": True, "cases": cases}))
            f.original(p)
            for fault in (
                "missing",
                "reorder",
                "count",
                "offset",
                "observation",
                "deadline",
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
                        "count": True,
                        "offset": True,
                        "observation": "other",
                        "deadline": 0,
                        "rows": 295,
                    }[fault]
                p.write_text(json.dumps(r))
                with self.subTest(fault=fault), self.assertRaises(ValueError):
                    f.original(p)

    def test_programming_and_observations_cross_the_actual_deadline(self):
        cases = self.cases()
        selected = {c["name"]: self.fixture(c) for c in cases}
        f.timelines(cases, selected)
        replacements = [
            ("20 W 0 117", "20 W 0 116"),
            ("21 W 1 5", "21 W 1 6"),
            ("1238944 W 1 23", "1238943 W 1 23"),
            ("1238947 R 3", "1238947 P 3"),
            ("1278946 W 0 125", "1278946 W 0 124"),
            ("1278946 W 1 55", "1278946 W 1 54"),
        ]
        for a, b in replacements:
            altered = {n: s.replace(a, b) for n, s in selected.items()}
            self.assertNotEqual(altered, selected)
            with self.subTest(missing=a), self.assertRaises(ValueError):
                f.timelines(cases, altered)

    def test_mutations_require_the_complete_current_targets(self):
        source = "\n".join(
            f"  assign when_Intel8271_l900{'_' + str(i) if i else ''} = (op == 4'b1101);"
            for i in range(3)
        )
        self.assertNotEqual(f.fault(source, "redispatch"), source)
        with self.assertRaises((ValueError, AssertionError)):
            f.fault(source.splitlines()[0], "redispatch")
        for name in ("pointer", "count"):
            text = "\n".join(
                [
                    "eventRegister0 = parameters_0;\neventParameterCount = 8'h03;\nspecifying <= 1'b1;\n  register0 <= parameters_0;\n  parameterCount <= 8'h03;"
                ]
                * 3
            )
            self.assertNotEqual(f.fault(text, name), text)
            with self.assertRaises((ValueError, AssertionError)):
                f.fault(
                    text + "\neventRegister0 = parameters_0;\neventParameterCount = 8'h03;", name
                )
        for name, (a, b) in f.KERNEL_FAULTS.items():
            self.assertEqual(f.kernel_fault(a, name), b)
            with self.assertRaises(ValueError):
                f.kernel_fault(a + a, name)


if __name__ == "__main__":
    unittest.main()
