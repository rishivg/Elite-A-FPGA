"""Full-bus evidence must not accept an empty or partial cold-boot seal."""

import unittest

from tools.bbc import boot_cache
from tools.bbc.boot_system_trace import require_complete_boot


class CompleteBootTests(unittest.TestCase):
    def test_all_required_checks_must_be_present_and_literal_true(self):
        checks = dict.fromkeys(
            (
                "complete",
                "ram_writes",
                "video_writes",
                "sound_events",
                "digital_audio",
                "raw_vsync",
                "final_ram",
                "unchanged_inputs",
            ),
            True,
        )
        report = {
            "passed": True,
            "full_boot": True,
            "requested_cycles": boot_cache.BOOT_CYCLES,
            "checks": checks,
        }
        require_complete_boot(report)
        damaged = [
            {**report, "checks": {}},
            {**report, "checks": None},
            {**report, "passed": 1},
            {**report, "full_boot": False},
            {**report, "requested_cycles": boot_cache.BOOT_CYCLES - 1},
            {key: value for key, value in report.items() if key != "checks"},
        ]
        for name in checks:
            damaged.append({**report, "checks": {k: v for k, v in checks.items() if k != name}})
            for wrong in (False, 1, "true", None):
                damaged.append({**report, "checks": {**checks, name: wrong}})
        for value in damaged:
            with self.subTest(value=value), self.assertRaises(ValueError):
                require_complete_boot(value)


if __name__ == "__main__":
    unittest.main()
