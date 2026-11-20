"""Reject incomplete byte-state cuts, lost fault targets and shortened histories."""

import copy
import json
import tempfile
import unittest
from pathlib import Path

from tools.bbc.proofs.cpu_reference import fdc_byte as f


class ByteControlTests(unittest.TestCase):
    def body(self):
        state = list(range(1000, 1934))
        names = {}
        offset = 0
        for name, width in f.FIELDS.items():
            names[name] = {"bits": state[offset : offset + width]}
            offset += width
        return {
            "ports": {"proofState": {"bits": state}, "proofNext": {"bits": state}},
            "netnames": names,
        }

    def test_complete_unique_state_and_canonical_irq_control(self):
        f.indices(self.body())
        for fault in ("missing", "duplicate", "alias", "foreign", "narrow", "shadow"):
            body = copy.deepcopy(self.body())
            if fault == "missing":
                body["ports"]["proofState"]["bits"].pop()
            elif fault == "duplicate":
                body["ports"]["proofState"]["bits"][-1] = 1000
            elif fault == "alias":
                body["netnames"]["byteCounterHigh"] = body["netnames"]["byteCounterLow"]
            elif fault == "foreign":
                body["netnames"]["mode"]["bits"][0] = 99999
            elif fault == "narrow":
                body["netnames"]["mode"]["bits"].pop()
            else:
                body["netnames"]["readIrqs"] = {"bits": [99999]}
            with self.subTest(fault=fault), self.assertRaises(ValueError):
                f.indices(body)

    def test_inventory_includes_all_irq_combinations_and_crc_boundaries(self):
        self.assertEqual(len(f.NAMES), 180)
        self.assertEqual(len(set(f.NAMES)), 180)
        self.assertTrue({name for name, _ in f.FAULTS.values()}.issubset(f.NAMES))
        self.assertEqual(len([n for n in f.NAMES if n.startswith("mode-")]), 144)
        for operation in (83, 75, 91):
            for offset in (127, 128, 129, 255, 256, 257):
                self.assertIn(f"last-{operation}-{offset}", f.NAMES)

    def test_history_metadata_cannot_skip_or_relabel_a_case(self):
        cases = []
        for name in f.NAMES:
            if name.startswith("mode-"):
                operation, mode, ack, offset = map(int, name.removeprefix("mode-").split("-", 3))
                cases.append(
                    dict(
                        name=name,
                        kind="mode",
                        operation=operation,
                        mode=mode,
                        ack=bool(ack),
                        offset=offset,
                        first=900000,
                        deadline=900128,
                        rows=30,
                    )
                )
            else:
                operation, offset = map(int, name.removeprefix("last-").split("-", 1))
                count = 4 if operation == 91 else 256
                cases.append(
                    dict(
                        name=name,
                        kind="last",
                        operation=operation,
                        offset=offset,
                        first=900000,
                        last=900000 + (count - 1) * 128,
                        count=count,
                        rows=300,
                    )
                )
        report = dict(passed=True, cases=cases)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "original.json"
            path.write_text(json.dumps(report))
            f.original(path)
            for fault in ("missing", "order", "verdict", "last", "rows", "mode", "ack"):
                changed = copy.deepcopy(report)
                if fault == "missing":
                    changed["cases"].pop()
                elif fault == "order":
                    changed["cases"].reverse()
                elif fault == "verdict":
                    changed["passed"] = False
                elif fault == "last":
                    changed["cases"][-1]["last"] += 1
                elif fault == "rows":
                    changed["cases"][0]["rows"] = 0
                elif fault == "mode":
                    changed["cases"][0]["mode"] += 1
                else:
                    changed["cases"][0]["ack"] = 1
                path.write_text(json.dumps(changed))
                with self.subTest(fault=fault), self.assertRaises(ValueError):
                    f.original(path)

    def test_rtl_and_kernel_faults_require_real_unique_targets(self):
        for name in f.FAULTS:
            with self.subTest(name=name), self.assertRaises(ValueError):
                f.fault("", name)
        for name in ("irq", "loss", "counter"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                f.kernel_fault("", name)
        source = "  assign byteProgress = callbackReady;"
        self.assertIn("io_address[2]", f.fault(source, "host-coupled-progress"))
        with self.assertRaises(ValueError):
            f.fault(source + "\n" + source, "host-coupled-progress")


if __name__ == "__main__":
    unittest.main()
