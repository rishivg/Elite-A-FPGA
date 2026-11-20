"""Reject partial or mislabeled interrupt-matrix successes."""

import subprocess
import unittest

from tools.bbc.cpu_interrupt_matrix import complete


class InterruptMatrixCompletionTests(unittest.TestCase):
    def result(self, text, code=0, error=""):
        return subprocess.CompletedProcess([], code, text, error)

    def test_complete_requested_period_passes(self):
        self.assertTrue(
            complete(
                self.result(
                    "Interrupt matrix: 96640 cases; 8697600 enabled cycles; period 16; PASS\n"
                ),
                16,
            )
        )

    def test_partial_matrix_and_wrong_clock_cannot_pass(self):
        for text in (
            "Interrupt matrix: 96639 cases; 8697600 enabled cycles; period 16; PASS\n",
            "Interrupt matrix: 96640 cases; 8697599 enabled cycles; period 16; PASS\n",
            "Interrupt matrix: 96640 cases; 8697600 enabled cycles; period 1; PASS\n",
        ):
            self.assertFalse(complete(self.result(text), 16))

    def test_failure_status_or_diagnostics_invalidate_success_text(self):
        text = "Interrupt matrix: 96640 cases; 8697600 enabled cycles; period 16; PASS\n"
        self.assertFalse(complete(self.result(text, code=1), 16))
        self.assertFalse(complete(self.result(text, error="BUS mismatch"), 16))


if __name__ == "__main__":
    unittest.main()
