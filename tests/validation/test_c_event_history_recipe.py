"""Keep the RTL and source event histories on the same observable contract."""

import re
import unittest
from unittest.mock import patch

from tools.bbc.proofs.cpu_reference import (
    cold_lda_event_history,
    event_history,
    frontend,
    rtl_event_history,
)


class EventHistoryRecipeTests(unittest.TestCase):
    def initial(self):
        state = dict.fromkeys((name for name, _ in frontend.FIELDS), 0)
        state.update(P=2, PINS=0x43000000, bcd_enabled=1)
        return state

    def test_bus_retirement_property_and_opcode_domain_are_identical(self):
        _, source = event_history.recipe(self.initial(), 0x43000000)
        prefix, actual = rtl_event_history.recipe(self.initial(), 0x43000000)
        observation = r"assign observedEquivalent=[\s\S]*?;"
        self.assertEqual(re.findall(observation, source), re.findall(observation, actual))
        domain = r"always @\* assume\([^\n]+\);"
        self.assertEqual(re.findall(domain, source), re.findall(domain, actual))
        self.assertIn(".offEvt(age>=26", prefix)
        self.assertIn("wire seedNmi=0,seedPrevious=0;", actual)

    def test_changed_observation_storage_boundary_is_rejected(self):
        with (
            patch.object(event_history, "recipe", return_value=("", "module Other;endmodule")),
            self.assertRaises(ValueError),
        ):
            rtl_event_history.recipe(self.initial(), 0x43000000)

    def test_cold_lda_prefix_keeps_the_same_observation_and_opcode_domain(self):
        _, original = rtl_event_history.recipe(self.initial(), 0x43000000)
        prefix, cold = cold_lda_event_history.recipe(self.initial(), 0x43000000)
        for pattern in (r"assign observedEquivalent=[\s\S]*?;", r"always @\* assume\([^\n]+\);"):
            self.assertEqual(re.findall(pattern, original), re.findall(pattern, cold))
        self.assertIn("a=8'h40,x=0,y=0,s=8'hfd,p=4", cold)
        self.assertIn("24:begin requestedAddress=base+16'd11;returnedData=8'ha9;end", prefix)
        self.assertIn("25:begin requestedAddress=base+16'd12;returnedData=8'h40;end", prefix)
        self.assertNotRegex(prefix, r"\bage\s*[!=<>]+\s*(26|28|29|30)\b")


if __name__ == "__main__":
    unittest.main()
