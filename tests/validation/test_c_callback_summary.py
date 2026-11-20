"""Keep conditional callback history proofs honest about their free domain."""

import copy
import unittest

from tools.bbc.proofs.cpu_reference import callback_summary


class CallbackSummaryDomainTests(unittest.TestCase):
    def graph(self):
        module = {"cells": {}, "netnames": {}}
        offset = 2
        for name, width, kind in (
            ("initialState", 256, "$anyconst"),
            *[
                (name, 1, "$anyseq")
                for name in (
                    "accepted",
                    "peripheralEvent",
                    "acknowledge",
                    "commandEvent",
                    "abortEvent",
                    "resetRequest",
                )
            ],
        ):
            bits = list(range(offset, offset + width))
            offset += width
            module["netnames"][name] = {"bits": bits, "attributes": {}}
            module["cells"][name] = {"type": kind, "connections": {"Y": bits}}
        return {"modules": {"CallbackSummary": module}}

    def test_full_initial_state_and_all_event_controls_remain_free(self):
        callback_summary.guard_domain(self.graph())

    def test_silent_domain_restrictions_are_rejected(self):
        for fault in (
            "assume",
            "check",
            "initialized-input",
            "constant-command",
            "narrow-state",
            "constant-reset",
            "missing-reset",
        ):
            graph = copy.deepcopy(self.graph())
            module = graph["modules"]["CallbackSummary"]
            if fault in ("assume", "check"):
                module["cells"]["restriction"] = {"type": "$" + fault}
            elif fault == "initialized-input":
                module["netnames"]["peripheralEvent"]["attributes"]["init"] = "0"
            elif fault == "constant-command":
                module["cells"]["commandEvent"]["type"] = "$anyconst"
            elif fault == "constant-reset":
                module["cells"]["resetRequest"]["type"] = "$anyconst"
            elif fault == "missing-reset":
                module["cells"]["resetRequest"]["connections"]["Y"] = [999]
            else:
                module["netnames"]["initialState"]["bits"].pop()
            with self.subTest(fault=fault), self.assertRaises(ValueError):
                callback_summary.guard_domain(graph)

    def test_reset_composition_requires_both_clocks_and_all_reset_controls(self):
        report = {
            "checks": [
                {"clock_multiplier": m, "name": name, "accepted": True, "register_bits": 934}
                for m in (1, 3)
                for name in (
                    "positive",
                    "callback-overrides-reset-mode",
                    "stale-phase",
                    "stale-parameters",
                )
            ],
            "source": {"dispatch_cases": 2048},
        }
        callback_summary.guard_reset_premise(report)
        for fault in (
            "missing-clock",
            "missing-control",
            "narrow-state",
            "unproved",
            "missing-dispatch",
        ):
            bad = copy.deepcopy(report)
            if fault == "missing-clock":
                bad["checks"] = bad["checks"][:4]
            elif fault == "missing-control":
                bad["checks"].pop()
            elif fault == "narrow-state":
                bad["checks"][0]["register_bits"] = 933
            elif fault == "unproved":
                bad["checks"][0]["accepted"] = False
            else:
                bad["source"]["dispatch_cases"] = 2047
            with self.subTest(fault=fault), self.assertRaises(ValueError):
                callback_summary.guard_reset_premise(bad)


if __name__ == "__main__":
    unittest.main()
