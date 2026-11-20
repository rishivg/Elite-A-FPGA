"""A failed restart proof qualifies a fault only with an observable witness."""

import json
import tempfile
import unittest
from pathlib import Path

from tools.bbc.proofs.cpu_reference.warm_checks import accepted_result


class WarmSourceChecksTest(unittest.TestCase):
    def setUp(self):
        self.workspace = tempfile.TemporaryDirectory()
        self.addCleanup(self.workspace.cleanup)
        self.witness = Path(self.workspace.name) / "witness.json"
        self.witness.write_text(
            json.dumps({"signal": [{"name": "faultObservable", "wave": "110"}]})
        )

    def fault(self, status, log):
        return accepted_result(status, log, self.witness, "faultObservable", False)

    def test_timeout_and_tool_failure_cannot_qualify_a_fault(self):
        for log in (
            "SAT proof finished - no model found: SUCCESS!",
            "ERROR: Timeout reached.",
            "ERROR: Failed to parse expression",
        ):
            with self.subTest(log=log):
                self.assertFalse(self.fault(1, log))

    def test_failed_proof_needs_its_actual_property_witness(self):
        log = "ERROR: Called with -verify and proof did fail!"
        self.assertTrue(self.fault(1, log))
        self.assertFalse(self.fault(0, log))
        self.witness.unlink()
        self.assertFalse(self.fault(1, log))
        for signal in (
            {"name": "observedEquivalent", "wave": "110"},
            {"name": "faultObservable", "wave": "11."},
        ):
            self.witness.write_text(json.dumps({"signal": [signal]}))
            self.assertFalse(self.fault(1, log))

    def test_positive_needs_solver_success_and_zero_exit(self):
        log = "SAT proof finished - no model found: SUCCESS!"
        self.assertTrue(accepted_result(0, log, self.witness, "definedObserved", True))
        self.assertFalse(accepted_result(1, log, self.witness, "definedObserved", True))
        self.assertFalse(accepted_result(0, "", self.witness, "definedObserved", True))


if __name__ == "__main__":
    unittest.main()
