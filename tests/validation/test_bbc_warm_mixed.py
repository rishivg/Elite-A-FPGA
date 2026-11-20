"""The one-request diagnostic must stop before its timing counter saturates."""

import unittest
from unittest.mock import patch

from tools.bbc.proofs.cpu_reference import warm_mixed


class WarmMixedDomainTest(unittest.TestCase):
    def test_invalid_horizons_and_timeouts_are_rejected_before_reading_premises(self):
        with patch.object(warm_mixed.memory_composition, "sealed") as sealed:
            for steps, timeout in ((4, 900), (32, 900), (64, 900), (24, 0), (24, 3601)):
                with self.subTest(steps=steps, timeout=timeout), self.assertRaises(ValueError):
                    warm_mixed.qualify(steps, timeout)
            sealed.assert_not_called()

    def test_valid_horizons_reach_the_premise_check(self):
        with patch.object(
            warm_mixed.memory_composition, "sealed", side_effect=RuntimeError("premise checked")
        ):
            for steps in (5, 18, 24, 31):
                with (
                    self.subTest(steps=steps),
                    self.assertRaisesRegex(RuntimeError, "premise checked"),
                ):
                    warm_mixed.qualify(steps)


if __name__ == "__main__":
    unittest.main()
