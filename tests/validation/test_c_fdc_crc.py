"""Reject incomplete CRC state, histories and mutation targets."""

import copy
import json
import tempfile
import unittest
from pathlib import Path

from tools.bbc.proofs.cpu_reference import fdc_crc as f


class CrcQualificationTests(unittest.TestCase):
    def body(self):
        state = list(range(1000, 1000 + f.WIDTH))
        fields, offset = {}, 0
        for name, width in f.FIELDS.items():
            fields[name] = {"bits": state[offset : offset + width]}
            offset += width
        return {
            "ports": {"proofState": {"bits": state}, "proofNext": {"bits": state}},
            "netnames": fields,
        }

    def test_complete_state_rejects_missing_aliased_and_foreign_bits(self):
        f.indices(self.body())
        for fault in ("missing", "duplicate", "alias", "foreign", "narrow"):
            body = copy.deepcopy(self.body())
            if fault == "missing":
                body["ports"]["proofState"]["bits"].pop()
            elif fault == "duplicate":
                body["ports"]["proofState"]["bits"][-1] = 1000
            elif fault == "alias":
                body["netnames"]["mode"] = body["netnames"]["command"]
            elif fault == "foreign":
                body["netnames"]["mode"]["bits"][0] = 99999
            else:
                body["netnames"]["mode"]["bits"].pop()
            with self.subTest(fault=fault), self.assertRaises(ValueError):
                f.indices(body)

    def test_history_inventory_requires_all_deadlines_and_retained_context(self):
        self.assertEqual(len(f.NAMES), 89)
        self.assertEqual(len(set(f.NAMES)), 89)
        self.assertTrue({n for n, _ in f.FAULTS.values()}.issubset(f.NAMES))
        cases = []
        for name in f.NAMES:
            c = {"name": name, "rows": 40, "first": 1200000}
            if name.startswith("search-id-command-"):
                c["command"], c["offset"] = map(
                    int, name.removeprefix("search-id-command-").split("-", 1)
                )
                c["deadline"] = 900000
            elif name == "retained-write-context":
                c["deadline"] = c["first"] + 640
            elif name.startswith("crc-irq-"):
                mode, ack, offset = map(int, name.removeprefix("crc-irq-").split("-", 2))
                c.update(mode=mode, ack=bool(ack), offset=offset, deadline=c["first"] + 512)
            elif name.startswith("header-irq-"):
                mode, ack = map(int, name.removeprefix("header-irq-").split("-"))
                c.update(mode=mode, ack=bool(ack), deadline=c["first"] + 128)
            elif name.startswith("header-stop-"):
                c.update(mode=int(name.removeprefix("header-stop-")), deadline=c["first"] + 128)
            else:
                c["mode"] = int(name.rsplit("-", 1)[1])
            cases.append(c)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "original.json"
            report = {"passed": True, "cases": cases}
            path.write_text(json.dumps(report))
            f.original(path)
            for fault in ("missing", "reorder", "rows", "mode", "deadline", "boolean", "verdict"):
                broken = copy.deepcopy(report)
                if fault == "missing":
                    broken["cases"].pop()
                elif fault == "reorder":
                    broken["cases"].reverse()
                elif fault == "rows":
                    broken["cases"][-1]["rows"] = True
                elif fault == "mode":
                    broken["cases"][-1]["mode"] = 0
                elif fault == "deadline":
                    broken["cases"][-1]["deadline"] += 1
                elif fault == "boolean":
                    next(c for c in broken["cases"] if c["name"].startswith("crc-irq-"))["ack"] = 1
                else:
                    broken["passed"] = False
                path.write_text(json.dumps(broken))
                with self.subTest(fault=fault), self.assertRaises(ValueError):
                    f.original(path)

    def test_missing_rtl_and_kernel_targets_fail_closed(self):
        for name in f.FAULTS:
            with self.subTest(name=name), self.assertRaises(ValueError):
                f.fault("", name)
        for name in ("raw-command", "crc", "wrapped-age"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                f.kernel_fault("", name)


if __name__ == "__main__":
    unittest.main()
