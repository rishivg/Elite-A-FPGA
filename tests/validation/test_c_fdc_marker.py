"""Reject narrowed marker state and incomplete public deadline histories."""

import copy
import json
import tempfile
import unittest
from pathlib import Path

from tools.bbc.proofs.cpu_reference import fdc_marker as f


class MarkerQualificationTests(unittest.TestCase):
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
        cases = []
        for name in f.NAMES:
            if name.startswith("deleted-result-"):
                c = {
                    "kind": "deleted",
                    "offset": int(name.removeprefix("deleted-result-")),
                    "rows": 292,
                }
            else:
                kind, _, op, cmd, offset = name.split("-", 4)
                c = {
                    "kind": kind,
                    "operation": int(op),
                    "command": int(cmd),
                    "offset": int(offset),
                    "rows": 38 if kind == "normal" else 299,
                }
            c.update(name=name, deadline=1205921 if c["kind"] == "normal" else 1605921)
            cases.append(c)
        return cases

    def test_all_state_and_fields_must_remain_complete(self):
        f.indices(self.body())
        for fault in ("missing", "duplicate", "foreign", "alias", "narrow"):
            b = copy.deepcopy(self.body())
            if fault == "missing":
                b["ports"]["proofState"]["bits"].pop()
            elif fault == "duplicate":
                b["ports"]["proofState"]["bits"][-1] = 1000
            elif fault == "foreign":
                b["netnames"]["mode"]["bits"][0] = 99999
            elif fault == "alias":
                b["netnames"]["mode"] = b["netnames"]["command"]
            else:
                b["ports"]["proofNext"]["bits"].pop()
            with self.subTest(fault=fault), self.assertRaises(ValueError):
                f.indices(b)

    def test_complete_histories_and_actual_deadlines_are_required(self):
        cases = self.cases()
        self.assertEqual(len(cases), 51)
        self.assertEqual(sum(c["rows"] for c in cases), 8964)
        self.assertTrue({v[0] for v in f.FAULTS.values()}.issubset(f.NAMES))
        with tempfile.TemporaryDirectory() as directory:
            p = Path(directory) / "original.json"
            p.write_text(json.dumps({"passed": True, "cases": cases}))
            f.original(p)
            for fault in (
                "missing",
                "reorder",
                "rows",
                "kind",
                "operation",
                "offset",
                "deadline",
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
                        "rows": 37,
                        "kind": "deleted",
                        "operation": True,
                        "offset": True,
                        "deadline": 0,
                    }[fault]
                p.write_text(json.dumps(r))
                with self.subTest(fault=fault), self.assertRaises(ValueError):
                    f.original(p)
        selected = {}
        for c in cases:
            result = c["name"].startswith("deleted-result-")
            t = c["deadline"] + c["offset"] - 2
            rows = [
                (t, "W", 0, 122),
                (t + 1, "W", 1, 22 if result else 31),
                (t + 2, "W", 1, 16 if result else c["command"]),
            ]
            rows.extend(
                (c["deadline"] + delta, action, address, 0)
                for delta, action, address in (
                    [(4, "R", 1), (129, "P", 0)]
                    if result
                    else [(d, "P", 0) for d in (4, 127, 128, 129, 255, 256, 257)]
                )
            )
            rows = [(17 + i, "P", 0, 0) for i in range(c["rows"] - len(rows))] + rows
            selected[c["name"]] = f"case 0 {c['name']} {len(rows)}\n" + "".join(
                f"{t} {a} {r} {d} 0 0 0\n" for t, a, r, d in rows
            )
        f.timelines(cases, selected)
        first = cases[0]
        name = first["name"]
        for broken in (
            selected[name].replace("W 1 31", "W 1 30"),
            selected[name].replace(f"{first['deadline'] + 128} P", f"{first['deadline'] + 130} P"),
        ):
            with self.assertRaises(ValueError):
                f.timelines(cases, {**selected, name: broken})

    def test_missing_mutation_targets_fail_closed(self):
        for name in f.FAULTS:
            with self.subTest(name=name), self.assertRaises(ValueError):
                f.fault("", name)
        for name in (
            "raw-command",
            "result",
            "crc",
            "register-write",
            "setter-or",
            "setter-ready",
            "irq-start",
            "external-status",
        ):
            with self.subTest(name=name), self.assertRaises(ValueError):
                f.kernel_fault("", name)


if __name__ == "__main__":
    unittest.main()
