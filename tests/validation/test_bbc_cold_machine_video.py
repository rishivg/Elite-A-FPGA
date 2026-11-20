"""Reject incomplete live video checks and ambiguous observation hooks."""

import unittest

from tools.bbc.cold_machine_video import DRIVER, complete, observer_driver


class LiveVideoCompletionTests(unittest.TestCase):
    def result(self, prefix=False):
        return {
            "passed": True,
            "prefix": prefix,
            "original_end": not prefix,
            "boundary_complete": True,
            "slots": 6,
            "physical_edges": 288,
            "sync_observations": 294,
            "character_samples": 3,
            "teletext_dots": 12,
            "expected_teletext_dots": 12,
            "bitmap_dots": 32,
            "expected_bitmap_dots": 32,
            "sync_differences": 0,
            "pixel_differences": 0,
            "control_differences": 0,
            "coordinate_differences": 0,
            "rgb_differences": 0,
            "unsupported_edges": 0,
        }

    def test_complete_full_capture_and_declared_prefix(self):
        self.assertTrue(complete(self.result(), 352, False))
        self.assertTrue(complete(self.result(True), 352, True))

    def test_prefix_cannot_claim_full_original_capture(self):
        self.assertFalse(complete(self.result(True), 352, False))
        result = self.result()
        result["original_end"] = False
        self.assertFalse(complete(result, 352, False))

    def test_missing_edges_slots_or_dots_cannot_pass(self):
        for field in (
            "physical_edges",
            "slots",
            "sync_observations",
            "character_samples",
            "teletext_dots",
            "bitmap_dots",
            "expected_teletext_dots",
            "expected_bitmap_dots",
        ):
            with self.subTest(field=field):
                result = self.result()
                result[field] -= 1
                self.assertFalse(complete(result, 352, False))

    def test_any_pin_difference_or_unsupported_edge_invalidates_success(self):
        for field in (
            "sync_differences",
            "pixel_differences",
            "control_differences",
            "coordinate_differences",
            "rgb_differences",
            "unsupported_edges",
        ):
            with self.subTest(field=field):
                result = self.result()
                result[field] = 1
                self.assertFalse(complete(result, 352, False))

    def test_missing_counts_boolean_counts_and_invalid_endpoints_are_rejected(self):
        result = self.result()
        del result["expected_bitmap_dots"]
        self.assertFalse(complete(result, 352, False))
        result = self.result()
        result["pixel_differences"] = False
        self.assertFalse(complete(result, 352, False))
        for cycles in (True, 352.0, 256, 351):
            self.assertFalse(complete(self.result(), cycles, False))


class PassiveObserverBindingTests(unittest.TestCase):
    def test_ordinary_driver_has_exactly_one_before_after_and_finish_hook(self):
        observed = observer_driver(DRIVER.read_text())
        for hook in (
            "coldVideo.before(master, dut);",
            "coldVideo.after(master, dut);",
            "coldVideo.finish(output, cycles, capturePrefix);",
        ):
            self.assertEqual(observed.count(hook), 1)

    def test_missing_or_duplicated_ordinary_driver_anchor_is_rejected(self):
        source = DRIVER.read_text()
        anchor = "        BBC_MACHINE_TYPE dut;"
        for replacement in ("", anchor + "\n" + anchor):
            with self.assertRaisesRegex(ValueError, "anchor changed"):
                observer_driver(source.replace(anchor, replacement))


if __name__ == "__main__":
    unittest.main()
