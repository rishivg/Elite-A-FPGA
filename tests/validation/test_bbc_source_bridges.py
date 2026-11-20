"""Source bridges reject changed source rules, initialization and generation seals."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.bbc import cpu_interrupt_proof, machine_clock_proof, memory_binding_proof
from tools.bbc.proofs import cpu_request_deadline, current_machine, display_history, memory_deadline
from tools.bbc.proofs.cpu_reference import frontend, reset_rtl
from tools.validation.common import ROOT, source_root


class DisplaySourceTests(unittest.TestCase):
    def setUp(self):
        self.source = (source_root("jsbeeb") / "src/video.js").read_text()

    def test_changed_threshold_or_missing_skew_retention_is_rejected(self):
        for before, after in (
            (
                "this.displayEnableSkew + (this.teletextMode ? 2 : 0)",
                "this.displayEnableSkew + (this.teletextMode ? 3 : 0)",
            ),
            ("if (skew < 3)", "if (skew <= 3)"),
        ):
            self.assertIn(before, self.source)
            with self.subTest(before=before), self.assertRaises(ValueError):
                display_history.source_expressions(self.source.replace(before, after))

    def test_reversed_display_set_clear_order_is_rejected(self):
        block = "if (this.horizCounter === displayEnablePos) {\n                this.dispEnableSet(SKEWDISPENABLE);\n            }"
        self.assertIn(block, self.source)
        altered = self.source.replace(block, "")
        tail = "this.dispEnableClear(HDISPENABLE | SKEWDISPENABLE);\n            }"
        self.assertIn(tail, altered)
        altered = altered.replace(tail, tail + "\n" + block)
        with self.assertRaisesRegex(ValueError, "ordering"):
            display_history.source_expressions(altered)

    def test_duplicate_source_statement_is_rejected(self):
        with self.assertRaises(ValueError):
            display_history.source_expressions(self.source + "\nconst displayEnablePos = 0;")


class NativeInitializationTests(unittest.TestCase):
    def test_incomplete_or_changed_native_profile_is_rejected(self):
        initial = {name: 0 for name, _ in frontend.FIELDS}
        initial.update(P=2, PINS=0x43000000, bcd_enabled=1)
        report = {"default_native_state": initial, "default_native_pins": 0x43000000}
        values = [initial[name] for name, _ in frontend.FIELDS] + [0x43000000]
        changed = list(values)
        changed[[name for name, _ in frontend.FIELDS].index("bcd_enabled")] = 0
        for data in (values[:-1], changed):
            with (
                self.subTest(data=data),
                patch.object(
                    reset_rtl.subprocess, "check_output", return_value=" ".join(map(str, data))
                ),
                self.assertRaises(ValueError),
            ):
                reset_rtl.native_initialization(Path("unused-native"), report)


class CurrentGenerationTests(unittest.TestCase):
    def test_empty_changed_or_nonboolean_generation_verdict_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            valid = {
                "passed": True,
                "inputs": {"source": "source-sha"},
                "artifacts": {"rtl": "rtl-sha"},
            }
            for changed in (
                {**valid, "artifacts": {}},
                {**valid, "passed": 1},
                {**valid, "inputs": {"source": "old-sha"}},
                {**valid, "artifacts": {"rtl": "old-sha"}},
            ):
                (directory / "generation.json").write_text(json.dumps(changed))
                with (
                    self.subTest(report=changed),
                    patch.object(current_machine, "DIRECTORY", directory),
                    patch.object(
                        current_machine, "generation_inputs", return_value=valid["inputs"]
                    ),
                    patch.object(
                        current_machine, "generation_outputs", return_value=valid["artifacts"]
                    ),
                    self.assertRaises(ValueError),
                ):
                    current_machine.audit()

    def test_default_parent_checks_still_require_current_root_generation(self):
        for producer in (
            cpu_request_deadline,
            memory_deadline,
            machine_clock_proof,
            memory_binding_proof,
        ):
            with (
                self.subTest(producer=producer.__name__),
                patch.object(producer.build, "generation_current", return_value=False),
                patch.object(producer.subprocess, "run") as solver,
            ):
                with self.assertRaises(ValueError):
                    producer.qualify()
                solver.assert_not_called()


class CpuObservationTests(unittest.TestCase):
    def test_custom_ports_do_not_change_other_proofs(self):
        core = (ROOT / "build/bbc/ArletCpuEnabled.v").read_text()
        wrapper = (ROOT / "rtl/bbc/BbcCpu.v").read_text()
        default = dict(cpu_interrupt_proof.PORTS)
        cpu_interrupt_proof.observed(core, wrapper, ports={**default, "Alu": ("[7:0]", "ADD")})
        self.assertEqual(cpu_interrupt_proof.PORTS, default)
        ordinary = cpu_interrupt_proof.observed(core, wrapper)
        self.assertNotIn("BBC_PROOF_Alu", ordinary)


if __name__ == "__main__":
    unittest.main()
