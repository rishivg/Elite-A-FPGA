"""Composition rejects altered observations, restricted inputs and stale leaves."""

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.bbc.proofs.cpu_reference import (
    cold_lda_event_history,
    event_composition,
    event_history,
    frontend,
)


class EventCompositionTests(unittest.TestCase):
    def initial(self):
        state = dict.fromkeys((name for name, _ in frontend.FIELDS), 0)
        state.update(P=2, PINS=0x43000000, bcd_enabled=1)
        return state

    def test_full_observation_specialization_preserves_all_model_equations(self):
        _, source = event_history.recipe(self.initial(), 0x43000000)
        _, actual = cold_lda_event_history.recipe(self.initial(), 0x43000000)
        self.assertEqual(event_composition.observation_contract(source), actual)
        changed = source.replace("nmiSample<=availableNmi||nmiRise;", "nmiSample<=nmiRise;")
        self.assertNotEqual(event_composition.observation_contract(changed), actual)

    def test_weakened_source_property_is_not_admitted_as_an_implication(self):
        _, source = event_history.recipe(self.initial(), 0x43000000)
        changed = source.replace(
            "observedEquivalent && (reset || stateRelation)",
            "observedEquivalent || (reset || stateRelation)",
        )
        with self.assertRaisesRegex(ValueError, "implication"):
            event_composition.observation_contract(changed)

    def test_nested_receipt_is_checked_even_when_parent_receipt_still_matches(self):
        with tempfile.TemporaryDirectory() as temporary:
            parent, child = [Path(temporary) / name / "result.json" for name in ("parent", "child")]
            for path in (parent, child):
                path.parent.mkdir()
                path.write_text(json.dumps({"inputs": {}}))
            reports = {
                parent: {"inputs": {str(child): "unchanged-child-receipt-hash"}},
                child: {"inputs": {}},
            }

            def sealed(path):
                if path == child:
                    raise ValueError("Changed child source")
                return reports[path]

            with (
                patch.object(event_composition.memory_composition, "sealed", side_effect=sealed),
                self.assertRaisesRegex(ValueError, "Changed child source"),
            ):
                event_composition.audit_tree([parent])

    def graph(self):
        module = {"netnames": {}, "cells": {}}
        for name, width, kind, start in (
            ("initialState", 256, "$anyconst", 10),
            ("nmi", 1, "$anyseq", 300),
            ("acceptedNmi", 1, "$anyseq", 301),
        ):
            bits = list(range(start, start + width))
            module["netnames"][name] = {"bits": bits, "attributes": {}}
            module["cells"][name] = {"type": kind, "connections": {"Y": bits}}
        return {"modules": {event_composition.TOP: module}}

    def test_summary_rejects_assumptions_and_initialized_or_constant_inputs(self):
        graph = self.graph()
        event_composition.guard_summary(graph)
        for fault in ("assumption", "initialized", "constant"):
            changed = copy.deepcopy(graph)
            module = changed["modules"][event_composition.TOP]
            if fault == "assumption":
                module["cells"]["extra"] = {"type": "$assume"}
            elif fault == "initialized":
                module["netnames"]["initialState"]["attributes"]["init"] = "0" * 256
            else:
                module["netnames"]["nmi"]["bits"] = ["0"]
            with self.subTest(fault=fault), self.assertRaises(ValueError):
                event_composition.guard_summary(changed)


if __name__ == "__main__":
    unittest.main()
