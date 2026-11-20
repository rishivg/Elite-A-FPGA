"""Reject incomplete READY state, reset evidence and public histories."""

import copy
import json
import tempfile
import unittest
from pathlib import Path

from tools.bbc.proofs.cpu_reference import fdc_ready as f


class ReadyQualificationTests(unittest.TestCase):
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

    def reset_body(self):
        b = {
            "ports": {"clk": {"bits": [1]}, "reset": {"bits": [2]}},
            "netnames": {
                "driveInLatched": {"bits": list(range(10, 18))},
                "latchedReady": {"bits": [18]},
            },
            "cells": {},
        }
        for name, q in (("latch", list(range(10, 18))), ("ready", [18])):
            b["cells"][name] = {
                "type": "$adff",
                "connections": {"CLK": [1], "ARST": [2], "Q": q},
                "parameters": {
                    "CLK_POLARITY": "1",
                    "ARST_POLARITY": "1",
                    "ARST_VALUE": "0" * len(q),
                },
            }
        return b

    def cases(self):
        return [
            dict(
                name=f"completion-ready-{value}",
                kind="early",
                value=value,
                deadline=1238945,
                rows=288,
            )
            for value in (0, 129, 4, 68)
        ] + [
            dict(
                name=f"completion-ready-{value}-{offset}",
                kind="timed",
                value=value,
                offset=offset,
                deadline=1238945,
                rows=287,
            )
            for value in (0, 129, 4, 68)
            for offset in (-1, 0, 1)
        ]

    def fixture(self, c):
        d = c["deadline"]
        timed = c["kind"] == "timed"
        start = d + c["offset"] - 2 if timed else d - 254
        rows = [(start, "W", 0, 0x7A), (start + 1, "W", 1, 27), (start + 2, "W", 1, c["value"])]
        if not timed:
            rows.append((d - 1, "P", 0, 0))
        result = 16 if (not timed or c["offset"] < 0) and not (c["value"] & 4) else 0
        rows.extend(
            [(d + (2 if timed else 1), "R", 1, result), (d + 128, "P", 0, 0), (d + 129, "W", 2, 1)]
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

    def test_complete_unique_state_is_required(self):
        f.indices(self.body())
        for fault in ("missing", "duplicate", "alias", "foreign", "narrow"):
            b = self.body()
            if fault == "missing":
                b["ports"]["proofState"]["bits"].pop()
            elif fault == "duplicate":
                b["ports"]["proofState"]["bits"][-1] = 1000
            elif fault == "alias":
                b["netnames"]["driveInLatched"] = b["netnames"]["driveOut"]
            elif fault == "foreign":
                b["netnames"]["latchedReady"]["bits"][0] = 99999
            else:
                b["ports"]["proofNext"]["bits"].pop()
            with self.subTest(fault=fault), self.assertRaises(ValueError):
                f.indices(b)

    def test_actual_async_reset_values_and_owners_are_required(self):
        self.assertEqual(f.reset_inventory(self.reset_body()), 9)
        for fault in (
            "missing",
            "clock",
            "reset",
            "polarity",
            "value",
            "width",
            "type",
            "duplicate",
            "alias",
        ):
            b = self.reset_body()
            if fault == "missing":
                del b["cells"]["ready"]
            elif fault == "clock":
                b["cells"]["ready"]["connections"]["CLK"] = [3]
            elif fault == "reset":
                b["cells"]["ready"]["connections"]["ARST"] = [3]
            elif fault == "polarity":
                b["cells"]["ready"]["parameters"]["ARST_POLARITY"] = "0"
            elif fault == "value":
                b["cells"]["ready"]["parameters"]["ARST_VALUE"] = "1"
            elif fault == "width":
                b["cells"]["ready"]["parameters"]["ARST_VALUE"] = "00"
            elif fault == "type":
                b["cells"]["ready"]["type"] = "$dff"
            elif fault == "duplicate":
                b["cells"]["copy"] = copy.deepcopy(b["cells"]["ready"])
            else:
                b["netnames"]["latchedReady"]["bits"] = [12]
            with self.subTest(fault=fault), self.assertRaises(ValueError):
                f.reset_inventory(b)

    def test_all_histories_and_both_error_and_ready_controls_are_required(self):
        cases = self.cases()
        self.assertEqual(len(cases), 16)
        self.assertEqual(sum(c["rows"] for c in cases), 4596)
        self.assertTrue({v[0] for v in f.FAULTS.values()}.issubset(f.NAMES))
        with tempfile.TemporaryDirectory() as directory:
            p = Path(directory) / "original.json"
            p.write_text(json.dumps({"passed": True, "cases": cases}))
            f.original(p)
            for fault in (
                "missing",
                "reorder",
                "value",
                "kind",
                "offset",
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
                elif fault == "offset":
                    r["cases"][4]["offset"] = True
                else:
                    r["cases"][0][fault] = {
                        "value": True,
                        "kind": "other",
                        "deadline": 0,
                        "rows": 287,
                    }[fault]
                p.write_text(json.dumps(r))
                with self.subTest(fault=fault), self.assertRaises(ValueError):
                    f.original(p)

    def test_programming_and_deadline_observations_cannot_be_dropped(self):
        cases = self.cases()
        selected = {c["name"]: self.fixture(c) for c in cases}
        f.timelines(cases, selected)
        for a, b in [
            ("1238691 W 0 122", "1238690 W 0 122"),
            ("W 1 27", "W 1 26"),
            ("1238943 W 0 122", "1238943 W 0 123"),
            ("1238947 R 1 16", "1238947 R 1 0"),
            ("1238946 R 1", "1238946 P 1"),
            ("1239073 P 0", "1239073 R 0"),
            ("1239074 W 2 1", "1239074 W 2 0"),
        ]:
            altered = {n: s.replace(a, b) for n, s in selected.items()}
            self.assertNotEqual(altered, selected)
            with self.subTest(missing=a), self.assertRaises(ValueError):
                f.timelines(cases, altered)

    def test_fault_targets_require_current_complete_inventory(self):
        source = "\n".join(
            f"  assign when_Intel8271_l892{'_' + str(i) if i else ''} = ((! driveIn[2]) || (! latchedReady));"
            for i in range(3)
        )
        self.assertNotEqual(f.fault(source, "ready-check"), source)
        with self.assertRaises(ValueError):
            f.fault(source.splitlines()[0], "ready-check")
        text = "driveInLatched <= specialData;\nlatchedReady <= specialData[2];"
        for name in ("register", "latch"):
            self.assertNotEqual(f.fault(text, name), text)
            with self.assertRaises(ValueError):
                f.fault(text + text, name)
        for name, (a, b) in f.KERNEL_FAULTS.items():
            self.assertEqual(f.kernel_fault(a, name), b)
            with self.assertRaises(ValueError):
                f.kernel_fault(a + a, name)


if __name__ == "__main__":
    unittest.main()
