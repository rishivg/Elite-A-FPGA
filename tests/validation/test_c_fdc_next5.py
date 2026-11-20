"""Reject incomplete source projections, state cuts and false negative controls."""

import copy
import subprocess
import unittest

from tools.bbc.proofs.cpu_reference import fdc_events, fdc_next5


class NextFiveTests(unittest.TestCase):
    def body(self):
        state = list(range(1000, 1934))
        fields, offset = {}, 0
        for name, width in fdc_next5.FIELDS.items():
            fields[name] = {"bits": state[offset : offset + width]}
            offset += width
        return {
            "ports": {"proofState": {"bits": state}, "proofNext": {"bits": state}},
            "netnames": fields,
        }

    def projection(self):
        return {
            "passed": True,
            "target_cases": 100663296,
            "head_cases": 262144,
            "budget": [
                ["finish", 24, 3] if i == 2 else ["retry", 1, (i + 1) & 255] for i in range(256)
            ],
            "step_ms": [3 if i == 0 else i * 2 for i in range(256)],
        }

    def test_cut_rejects_missing_aliased_or_narrow_state(self):
        fdc_next5.inventory(self.body())
        for fault in ("missing", "next", "alias", "outside", "width"):
            body = self.body()
            if fault == "missing":
                body["ports"]["proofState"]["bits"] = body["ports"]["proofState"]["bits"][:-1]
            elif fault == "next":
                body["ports"]["proofNext"]["bits"] = []
            elif fault == "alias":
                body["netnames"]["mmioData"] = body["netnames"]["mmioClocks"]
            elif fault == "outside":
                body["netnames"]["specifying"]["bits"] = [99999]
            else:
                body["netnames"]["parameterCount"]["bits"].pop()
            with self.subTest(fault=fault), self.assertRaises(ValueError):
                fdc_next5.inventory(body)

    def test_source_projection_requires_full_byte_domain_and_terminal_increment(self):
        fdc_next5.source_projection(self.projection())
        for fault in ("target", "head", "budget", "rate", "terminal", "wrap"):
            bad = copy.deepcopy(self.projection())
            if fault in ("target", "head"):
                bad[fault + "_cases"] -= 1
            elif fault == "budget":
                bad["budget"].pop()
            elif fault == "rate":
                bad["step_ms"][0] = 0
            elif fault == "terminal":
                bad["budget"][2][2] = 2
            else:
                bad["budget"][255][2] = 256
            with self.subTest(fault=fault), self.assertRaises(ValueError):
                fdc_next5.source_projection(bad)

    def test_faults_require_unique_real_assignments(self):
        source = "  assign seekPostDelay = liveDelay;\n"
        self.assertIn("liveDelay >> 1", fdc_next5.fault(source, "timing"))
        for bad in ("", source + source):
            with self.subTest(source=bad), self.assertRaises(ValueError):
                fdc_next5.fault(bad, "timing")
        with self.assertRaises(ValueError):
            fdc_next5.fault("", "retry")
        with self.assertRaises(ValueError):
            fdc_next5.fault("", "stale-acceptance")

    def test_stale_acceptance_fault_preserves_callback_driver(self):
        source = """  assign a = (eventAcceptingParameters && b);
  assign c = eventAcceptingParameters;
  always @(*) eventAcceptingParameters = acceptingParameters;
"""
        broken = fdc_next5.fault(source, "stale-acceptance")
        self.assertIn("assign a = (acceptingParameters && b)", broken)
        self.assertIn("always @(*) eventAcceptingParameters = acceptingParameters", broken)

    def test_both_header_retirement_retry_sites_are_mutated(self):
        source = (
            "  assign first = (nextRetryCount == 8'h03);\n"
            "  assign second = (nextRetryCount == 8'h03);\n"
        )
        broken = fdc_next5.fault(source, "retry")
        self.assertEqual(broken.count("nextRetryCount == 8'h02"), 2)
        self.assertNotIn("nextRetryCount == 8'h03", broken)
        self.assertEqual(fdc_events.retry_fault(source), broken)
        for incomplete in (source.splitlines()[0], source + source):
            with self.subTest(source=incomplete), self.assertRaises(ValueError):
                fdc_events.retry_fault(incomplete)

    def test_native_fault_requires_named_behavioral_difference(self):
        row = "FDC_EPOCH_MISMATCH spec-late-1 cycle 16130 return 2/1 nmi 0/0 edges 1/1\n"
        self.assertTrue(
            fdc_next5.negative_result(subprocess.CompletedProcess([], 1, row, ""), "parameters")
        )
        for code, out, err in (
            (0, row, ""),
            (2, row, ""),
            (1, row, "timeout"),
            (1, row.replace("2/1", "1/1"), ""),
            (1, row.replace("spec-late-1", "other"), ""),
            (1, "Missing fixture or SSD\n", ""),
        ):
            self.assertFalse(
                fdc_next5.negative_result(
                    subprocess.CompletedProcess([], code, out, err), "parameters"
                )
            )


if __name__ == "__main__":
    unittest.main()
