"""Keep positive theorem reuse restricted to read-only observation changes."""

import unittest

from tools.bbc.proofs.cpu_reference.source_history_faults import guard_observer
from tools.bbc.proofs.cpu_reference.warm_checks import observed_contract


class SourceObserverTests(unittest.TestCase):
    SOURCE = """module Example;
wire [255:0] cState,cHistory;
Prefix actual(.proofResponse(cResponse));
assign equivalent=observedEquivalent && (reset || stateRelation);
endmodule
"""

    def observer(self):
        return observed_contract(self.SOURCE).replace(
            "reset || cTrap || observedEquivalent",
            "reset || cTrap || !sourceContext || observedEquivalent",
        )

    def test_read_only_observer_restores_the_original_contract(self):
        guard_observer(self.SOURCE, self.observer())

    def test_changed_driver_property_or_trap_observation_is_rejected(self):
        text = self.observer()
        for before, after in (
            ("actual(.proofTrap(cTrap)", "actual(.proofTrap(otherTrap)"),
            ("equivalent=observedEquivalent &&", "equivalent=observedEquivalent ||"),
            ("sourceContext && observedEquivalent", "observedEquivalent"),
        ):
            self.assertIn(before, text)
            with self.subTest(before=before), self.assertRaises(ValueError):
                guard_observer(self.SOURCE, text.replace(before, after))


if __name__ == "__main__":
    unittest.main()
