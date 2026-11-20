"""Reject incomplete state, missing mutations and reversed source callback order."""

import copy
import json
import tempfile
import unittest
from pathlib import Path

from tools.bbc.proofs.cpu_reference import fdc_order


class TimerIndexOrderTests(unittest.TestCase):
    def body(self):
        state = list(range(1000, 1934))
        names, offset = {}, 0
        for name, width in fdc_order.FIELDS.items():
            names[name] = {"bits": state[offset : offset + width]}
            offset += width
        return {
            "ports": {"proofState": {"bits": state}, "proofNext": {"bits": state}},
            "netnames": names,
        }

    def test_inventory_rejects_aliases_missing_and_foreign_state(self):
        fdc_order.indices(self.body())
        for fault in ("missing", "duplicate", "alias", "foreign", "narrow"):
            body = copy.deepcopy(self.body())
            if fault == "missing":
                body["ports"]["proofState"]["bits"].pop()
            elif fault == "duplicate":
                body["ports"]["proofState"]["bits"][-1] = 1000
            elif fault == "alias":
                body["netnames"]["indexCount"] = body["netnames"]["headRate"]
            elif fault == "foreign":
                body["netnames"]["headRate"]["bits"][0] = 99999
            else:
                body["netnames"]["indexCallback"]["bits"].pop()
            with self.subTest(fault=fault), self.assertRaises(ValueError):
                fdc_order.indices(body)

    def test_original_requires_real_timer_before_index_at_every_tie(self):
        deadline = 1200033
        cases = []
        for name in fdc_order.NAMES:
            tie = name in fdc_order.NAMES[:36]
            events = [[deadline, "timer"]]
            if name != "105-0-0":
                events.append([deadline, "index"])
            cases.append(
                {"name": name, "deadline": deadline, "tie": tie, "events": events, "rows": 1}
            )
        cases[-1].update({"edges": 2, "rephase": 10, "next": 11})
        original = {"passed": True, "cases": cases, "deadline": deadline, "step_rates": [3] * 256}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "original.json"
            path.write_text(json.dumps(original))
            fdc_order.original(path)
            for fault in (
                "reverse",
                "missing",
                "rates",
                "inventory",
                "verdict",
                "untagged",
                "rows",
                "rephase",
            ):
                changed = copy.deepcopy(original)
                if fault == "reverse":
                    changed["cases"][4]["events"].reverse()
                elif fault == "missing":
                    changed["cases"][4]["events"].pop()
                elif fault == "rates":
                    changed["step_rates"].pop()
                elif fault == "inventory":
                    changed["cases"].pop()
                elif fault == "verdict":
                    changed["passed"] = False
                elif fault == "untagged":
                    changed["cases"][4]["tie"] = False
                elif fault == "rows":
                    changed["cases"][4]["rows"] = 0
                else:
                    changed["cases"][-1]["next"] += 1
                path.write_text(json.dumps(changed))
                with self.subTest(fault=fault), self.assertRaises(ValueError):
                    fdc_order.original(path)

    def test_actual_mutation_targets_must_be_unique_and_present(self):
        for name in fdc_order.FAULTS:
            with self.subTest(name=name), self.assertRaises(ValueError):
                fdc_order.fault("", name)
        selector = "\n".join(f"  assign a{i} = (preIndexCallback == 2'b01);" for i in range(4))
        self.assertNotIn("preIndexCallback", fdc_order.fault(selector, "stale-selector"))
        with self.assertRaises(ValueError):
            fdc_order.fault(selector + "\n" + selector, "stale-selector")
        count = "\n".join(f"  assign a{i} = (preIndexCount == 8'h01);" for i in range(5))
        self.assertNotIn("preIndexCount", fdc_order.fault(count, "stale-count"))

    def test_all_registers_and_fault_histories_are_included(self):
        self.assertEqual(len(fdc_order.NAMES), 102)
        self.assertEqual(len(set(fdc_order.NAMES)), 102)
        self.assertEqual(set(fdc_order.NAMES[36:100]), {f"special-{i}" for i in range(64)})
        self.assertTrue({case for case, _ in fdc_order.FAULTS.values()}.issubset(fdc_order.NAMES))


if __name__ == "__main__":
    unittest.main()
