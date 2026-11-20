"""Mechanical callback certificates reject narrowed state and vacuous controls."""

import copy
import subprocess
import unittest

from tools.bbc.proofs.cpu_reference import fdc_commands, fdc_events


class CommandContextTests(unittest.TestCase):
    def body(self):
        state = list(range(1000, 1934))
        names, offset = {}, 0
        for name, width in fdc_commands.FIELDS.items():
            names[name] = {"bits": state[offset : offset + width]}
            offset += width
        return {"ports": {"proofState": {"bits": state}}, "netnames": names}

    def test_complete_unique_controller_context_is_required(self):
        result = fdc_commands.indices(self.body())
        self.assertEqual(len(result["command"]), 8)
        for fault in ("short-state", "aliased-owner", "short-command", "not-state"):
            body = copy.deepcopy(self.body())
            if fault == "short-state":
                body["ports"]["proofState"]["bits"].pop()
            elif fault == "aliased-owner":
                body["netnames"]["mechanicalOp"] = body["netnames"]["phase_1"]
            elif fault == "short-command":
                body["netnames"]["command"]["bits"].pop()
            else:
                body["netnames"]["latchedReady"]["bits"] = [9999]
            with self.subTest(fault=fault), self.assertRaises(ValueError):
                fdc_commands.indices(body)

    def test_all_mechanical_context_consumers_must_be_present(self):
        source = "assign callback = (mechanicalOp == 4'b1010);\n" * 4
        self.assertEqual(fdc_commands.context_fault(source).count("op == 4'b1010"), 4)
        for bad in (
            source + source,
            source.splitlines()[0],
            source.replace("mechanicalOp", "otherOp"),
        ):
            with self.subTest(source=bad), self.assertRaises(ValueError):
                fdc_commands.context_fault(bad)

    def test_native_fault_requires_the_exact_premature_completion(self):
        row = "FDC_EPOCH_MISMATCH read-partial--1 cycle 858040 return 24/144 nmi 1/0 edges 1/0\n"
        self.assertTrue(fdc_commands.negative_result(subprocess.CompletedProcess([], 1, row, "")))
        for code, output, error in (
            (0, row, ""),
            (2, row, ""),
            (1, row, "timeout"),
            (1, row.replace("read-partial--1", "other"), ""),
            (1, row.replace("edges 1/0", "edges 0/0"), ""),
            (1, row.replace("return 24/144", "return 0/144"), ""),
            (1, "Missing fixture or SSD\n", ""),
        ):
            with self.subTest(output=output):
                self.assertFalse(
                    fdc_commands.negative_result(
                        subprocess.CompletedProcess([], code, output, error)
                    )
                )

    def test_shared_fixture_parser_keeps_the_declared_inventory(self):
        text = "".join(
            f"case {i} {name} 1\n17 P 0 0 0 0 0\n" for i, name in enumerate(fdc_commands.NAMES)
        )
        self.assertEqual(list(fdc_events.fixtures(text, fdc_commands.NAMES)), fdc_commands.NAMES)
        for bad, names in (
            (text, None),
            (text, list(reversed(fdc_commands.NAMES))),
            (text[: text.rfind("case")], fdc_commands.NAMES),
        ):
            with self.subTest(text=bad), self.assertRaises(ValueError):
                fdc_events.fixtures(bad, names)


if __name__ == "__main__":
    unittest.main()
