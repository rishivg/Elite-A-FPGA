"""Reject prefixes and incomplete restart corpora."""

import subprocess
import unittest

from tools.bbc.cpu_warm_reset import CASES, complete


class RestartCompletionTests(unittest.TestCase):
    def result(self, mode="normal", period=16, *, cases=None, cycles=None):
        cases = CASES[mode] if cases is None else cases
        cycles = CASES[mode] * 90 if cycles is None else cycles
        return subprocess.CompletedProcess(
            [],
            0,
            f"Warm reset {mode}: {cases} cases; {cycles} enabled cycles; period {period}; PASS\n",
            "",
        )

    def test_all_declared_modes_and_periods(self):
        for mode in CASES:
            for period in (1, 16, 48):
                self.assertTrue(complete(self.result(mode, period), mode, period))

    def test_passing_prefix_cannot_replace_complete_case_or_cycle_count(self):
        for mode, cases in CASES.items():
            self.assertFalse(complete(self.result(mode, cases=cases - 1), mode, 16))
            self.assertFalse(complete(self.result(mode, cycles=cases * 90 - 1), mode, 16))

    def test_clock_or_corpus_label_cannot_be_substituted(self):
        self.assertFalse(complete(self.result("branch"), "normal", 16))
        self.assertFalse(complete(self.result(period=1), "normal", 16))
        for period in (True, 16.0, 32):
            self.assertFalse(complete(self.result(), "normal", period))
        self.assertFalse(complete(self.result(), "unknown", 16))

    def test_failure_diagnostics_and_extra_success_lines_invalidate_pass(self):
        result = self.result()
        result.returncode = 1
        self.assertFalse(complete(result, "normal", 16))
        result = self.result()
        result.stderr = "BUS FAIL example"
        self.assertFalse(complete(result, "normal", 16))
        result = self.result()
        result.stdout += "PASS\n"
        self.assertFalse(complete(result, "normal", 16))


if __name__ == "__main__":
    unittest.main()
