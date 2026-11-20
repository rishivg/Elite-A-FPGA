"""Timer certificates must retain symbolic state and meaningful failure controls."""

import copy
import subprocess
import unittest

from tools.bbc.proofs.cpu_reference import fdc_timers


class TimerTests(unittest.TestCase):
    def body(self):
        state = list(range(1000, 1934))
        fields, offset = {}, 0
        for name, width in fdc_timers.FIELDS.items():
            fields[name] = {"bits": state[offset : offset + width]}
            offset += width
        return {"ports": {"proofState": {"bits": state}}, "netnames": fields}

    def test_timer_cut_rejects_missing_aliased_or_narrow_state(self):
        fdc_timers.state_indices(self.body())
        for fault in ("missing", "aliased", "narrow", "outside"):
            body = self.body()
            if fault == "missing":
                body["ports"]["proofState"]["bits"].pop()
            elif fault == "aliased":
                body["netnames"]["stepRate"] = body["netnames"]["command"]
            elif fault == "narrow":
                body["netnames"]["mechanicalDelay"]["bits"].pop()
            else:
                body["netnames"]["acceptingParameters"]["bits"] = [99999]
            with self.subTest(fault=fault), self.assertRaises(ValueError):
                fdc_timers.state_indices(body)

    def test_all_original_rates_and_scheduler_cases_are_required(self):
        source = {
            "passed": True,
            "cases": 12288,
            "step_ms": [3 if i == 0 else i * 2 for i in range(256)],
        }
        function = fdc_timers.source_rates(source)
        self.assertIn("8'd0:sourceStep=24'd96000;", function)
        self.assertIn("8'd255:sourceStep=24'd16320000;", function)
        for fault in ("missing-rate", "missing-case", "float", "boolean", "zero"):
            bad = copy.deepcopy(source)
            if fault == "missing-rate":
                bad["step_ms"].pop()
            elif fault == "missing-case":
                bad["cases"] = 12287
            else:
                bad["step_ms"][0] = {"float": 3.0, "boolean": True, "zero": 0}[fault]
            with self.subTest(fault=fault), self.assertRaises(ValueError):
                fdc_timers.source_rates(bad)

    def test_cancellation_fault_must_target_one_actual_command_guard(self):
        source = "if(writeSpecialStarts) begin\n        mmioClocks <= temporary;\n"
        self.assertIn("mechanicalPhase <= Mechanical_Idle;", fdc_timers.timer_fault(source))
        for bad in (source + source, source.replace("writeSpecialStarts", "other")):
            with self.subTest(source=bad), self.assertRaises(ValueError):
                fdc_timers.timer_fault(bad)

    def test_native_control_requires_the_exact_lost_old_completion(self):
        row = "FDC_EPOCH_MISMATCH rate--1 cycle 874038 return 0/24 nmi 0/1 edges 0/1\n"
        self.assertTrue(fdc_timers.negative_result(subprocess.CompletedProcess([], 1, row, "")))
        for code, out, err in (
            (0, row, ""),
            (2, row, ""),
            (1, row, "timeout"),
            (1, row.replace("edges 0/1", "edges 1/1"), ""),
            (1, row.replace("rate--1", "other"), ""),
            (1, "Missing fixture or SSD\n", ""),
        ):
            with self.subTest(output=out):
                self.assertFalse(
                    fdc_timers.negative_result(subprocess.CompletedProcess([], code, out, err))
                )


if __name__ == "__main__":
    unittest.main()
