"""Reset certificates must retain the actual all-state controller domain."""

import copy
import unittest

from tools.bbc.proofs.cpu_reference import fdc_reset


class ResetInventoryTests(unittest.TestCase):
    def body(self):
        state = list(range(1000, 1934))
        offset, names = 0, {}
        for name, width in fdc_reset.FIELDS.items():
            names[name] = {"bits": state[offset : offset + width]}
            offset += width
        return {
            "ports": {
                "proofState": {"direction": "input", "bits": state},
                "proofNext": {"direction": "output", "bits": list(range(2000, 2934))},
            },
            "netnames": names,
        }

    def test_reset_projection_uses_unique_actual_state_fields(self):
        result = fdc_reset.fields(self.body())
        self.assertEqual(result["phase_1"], [0, 1, 2, 3])
        self.assertEqual(sum(map(len, result.values())), sum(fdc_reset.FIELDS.values()))

    def test_incomplete_or_aliased_projection_fails_closed(self):
        for fault in ("missing-state", "missing-next", "alias", "narrow", "not-state"):
            body = copy.deepcopy(self.body())
            if fault == "missing-state":
                body["ports"]["proofState"]["bits"].pop()
            elif fault == "missing-next":
                body["ports"]["proofNext"]["bits"].pop()
            elif fault == "alias":
                body["netnames"]["nmiLevel"] = body["netnames"]["busy"]
            elif fault == "narrow":
                body["netnames"]["mode"]["bits"].pop()
            else:
                body["netnames"]["busy"]["bits"] = [99999]
            with self.subTest(fault=fault), self.assertRaises(ValueError):
                fdc_reset.fields(body)

    def test_faults_change_only_the_guarded_reset_assignments(self):
        source = """localparam Phase_Idle = 4'd0;
if(resetWork) begin
  readDataPending <= 1'b0;
  acceptingParameters <= 1'b0;
  rotation <= nextRotation;
  phase_1 <= Phase_Idle;
  readyPulses <= 2'b00;
  mode <= 8'h0;
end
"""
        variants = dict(fdc_reset.reset_variants(source))
        self.assertEqual(len(variants), 4)
        self.assertEqual(variants["positive"], source)
        self.assertNotIn("mode <=", variants["callback-overrides-reset-mode"])
        self.assertIn("phase_1 <= phase_1;", variants["stale-phase"])
        self.assertIn("acceptingParameters <= acceptingParameters;", variants["stale-parameters"])
        for bad in (
            source + source,
            source.replace("4'd0", "4'd1"),
            source.replace("readDataPending", "other"),
        ):
            with self.subTest(source=bad), self.assertRaises(ValueError):
                fdc_reset.reset_variants(bad)


if __name__ == "__main__":
    unittest.main()
