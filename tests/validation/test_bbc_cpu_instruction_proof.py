import copy
import json
import tempfile
import unittest
from pathlib import Path

from tools.bbc.cpu_instruction_proof import (
    CONTROL_OPCODES,
    FAULTS,
    SCALAR_OPCODES,
    cache_current,
    guard_assumptions,
    memory_groups,
    mutated,
    supporting_evidence,
)
from tools.validation.common import ROOT, digest


class CpuInstructionProofGuards(unittest.TestCase):
    def test_cache_requires_complete_bounded_proofs_and_unchanged_artifacts(self):
        source = (ROOT / "sim/formal/BbcCpuMemoryContract.v").read_text()
        inventory = (ROOT / "tools/bbc/reference/official_stimulus.h").read_text()
        groups = [("proof", SCALAR_OPCODES), ("control-proof", CONTROL_OPCODES)] + [
            (f"memory-mode{mode}", ops) for mode, ops, _ in memory_groups(source, inventory)
        ]
        proofs = [
            {
                "name": name,
                "passed": True,
                "nonempty_domains": True,
                "opcodes": [f"{op:02x}" for op in ops],
            }
            for name, ops in groups
        ]
        opcodes = sorted(op for p in proofs for op in p["opcodes"])
        with tempfile.TemporaryDirectory() as name:
            artifact = Path(name) / "proof.log"
            artifact.write_bytes(b"sealed solver observation")
            report = {
                "schema": 1,
                "passed": True,
                "inputs_unchanged": True,
                "opcode_coverage_complete": True,
                "universal_cpu_equivalence": False,
                "universal_game_equivalence": False,
                "inputs": {"producer": "declared source"},
                "tools": {"yosys": "declared tool"},
                "opcodes": opcodes,
                "proofs": proofs,
                "negative_controls": [{"name": f[0], "detected": True} for f in FAULTS],
                "artifacts": {str(artifact): digest(artifact)},
            }

            def current(r):
                return cache_current(r, report["inputs"], report["tools"], opcodes)

            self.assertTrue(current(report))
            for bad in (
                {**report, "passed": 1},
                {**report, "inputs_unchanged": False},
                {**report, "universal_cpu_equivalence": True},
                {**report, "universal_game_equivalence": True},
                {**report, "opcodes": opcodes[:-1]},
                {**report, "proofs": proofs[:-1]},
                {**report, "negative_controls": report["negative_controls"][:-1]},
                {**report, "artifacts": {}},
            ):
                with self.subTest(bad=bad):
                    self.assertFalse(current(bad))
            changed = copy.deepcopy(report)
            changed["proofs"][0]["nonempty_domains"] = False
            self.assertFalse(current(changed))
            changed = copy.deepcopy(report)
            changed["negative_controls"][0]["detected"] = 1
            self.assertFalse(current(changed))
            artifact.write_bytes(b"changed solver observation")
            self.assertFalse(current(report))

    def test_independent_evidence_requires_every_clock_and_unchanged_bytes(self):
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            artifact = directory / "capture"
            artifact.write_bytes(b"independently executed CPU observations")
            hashes = {str(artifact): digest(artifact)}
            report = {
                "passed": True,
                "inputs_unchanged": True,
                "inputs": hashes,
                "artifacts": hashes,
                "periods": [{"period": p, "passed": True} for p in (1, 16, 48)],
            }
            path = directory / "result.json"
            path.write_text(json.dumps(report))
            self.assertEqual(supporting_evidence(path), report)
            nested = {**report, "inputs": {"sources": hashes, "compiler": "declared compiler"}}
            path.write_text(json.dumps(nested))
            self.assertEqual(supporting_evidence(path), nested)
            for bad in (
                {**report, "passed": 1},
                {**report, "inputs_unchanged": False},
                {**report, "periods": report["periods"][:2]},
                {**report, "inputs": {}},
                {**report, "artifacts": {}},
            ):
                path.write_text(json.dumps(bad))
                with self.subTest(bad=bad), self.assertRaises(ValueError):
                    supporting_evidence(path)
            path.write_text(json.dumps(report))
            artifact.write_bytes(b"changed observations")
            with self.assertRaises(ValueError):
                supporting_evidence(path)

    def test_negative_control_requires_one_actual_mutation_site(self):
        self.assertEqual(
            mutated("prefix correct suffix", "correct", "wrong"), "prefix wrong suffix"
        )
        for actual in ("missing", "correct correct"):
            with self.subTest(actual=actual), self.assertRaises(ValueError):
                mutated(actual, "correct", "wrong")

    def test_domain_assumption_cannot_restrict_another_input_or_state(self):
        graph = {
            "modules": {
                "contract": {
                    "netnames": {"opcode": {"bits": list(range(8))}},
                    "cells": {
                        "opcode": {
                            "type": "$anyconst",
                            "port_directions": {"Y": "output"},
                            "connections": {"Y": list(range(8))},
                        },
                        "allowed": {
                            "type": "$reduce_or",
                            "port_directions": {"A": "input", "Y": "output"},
                            "connections": {"A": list(range(8)), "Y": [8]},
                        },
                        "assume": {
                            "type": "$assume",
                            "port_directions": {"A": "input", "EN": "input"},
                            "connections": {"A": [8], "EN": ["1"]},
                        },
                    },
                }
            }
        }
        guard_assumptions(graph, "contract")
        for mutation in ("extra-input", "state", "conditional", "additional"):
            bad = copy.deepcopy(graph)
            cells = bad["modules"]["contract"]["cells"]
            if mutation == "extra-input":
                cells["data"] = {
                    "type": "$anyconst",
                    "port_directions": {"Y": "output"},
                    "connections": {"Y": [9]},
                }
                cells["allowed"]["connections"]["A"].append(9)
            elif mutation == "state":
                cells["allowed"]["type"] = "$dff"
            elif mutation == "conditional":
                cells["assume"]["connections"]["EN"] = [0]
            else:
                cells["second-assumption"] = copy.deepcopy(cells["assume"])
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                guard_assumptions(bad, "contract")

    def test_memory_grouping_requires_all_original_opcode_facts(self):
        source = (ROOT / "sim/formal/BbcCpuMemoryContract.v").read_text()
        inventory = (ROOT / "tools/bbc/reference/official_stimulus.h").read_text()
        groups = memory_groups(source, inventory)
        self.assertEqual([mode for mode, _, _ in groups], list(range(1, 9)))
        self.assertEqual(sum(len(ops) for _, ops, _ in groups), 100)
        first = next(line for line in source.splitlines() if "8'h01:begin mode=" in line)
        for bad in (
            source.replace(first, ""),
            source.replace(first, first + "\n" + first),
            source.replace(first, first.replace("mode=7;", "mode=9;")),
            source.replace(first, first.replace("semanticOpcode=8'h09;", "semanticOpcode=8'h29;")),
        ):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                memory_groups(bad, inventory)


if __name__ == "__main__":
    unittest.main()
