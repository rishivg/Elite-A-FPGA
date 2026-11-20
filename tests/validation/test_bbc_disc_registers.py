"""Reject incomplete register evidence and faults outside the actual RTL."""

import subprocess
import unittest
from copy import deepcopy

from tools.bbc.disc_registers import (
    PREFIXES,
    TRANSFERS,
    complete,
    conflated_nmi,
    disconnected_header,
    validate_cases,
)


class DiscRegisterTests(unittest.TestCase):
    def test_prefix_inventory_requires_every_address_and_own_fixture(self):
        good = {
            "own_cold_cases": True,
            "cases": [
                {
                    "prefix": prefix,
                    "address": address,
                    "rows": rows,
                    "fixture": f"original-registers.json.{prefix}-{address}.tsv",
                }
                for prefix, rows in PREFIXES.items()
                for address in range(32)
            ],
        }
        self.assertEqual(len(validate_cases(good, "registers")), 160)
        for mutate in (
            lambda m: m["cases"].pop(),
            lambda m: m["cases"].reverse(),
            lambda m: m["cases"][0].update(address=True),
            lambda m: m["cases"][0].update(fixture="../other.tsv"),
            lambda m: m.update(own_cold_cases=False),
        ):
            bad = deepcopy(good)
            mutate(bad)
            with self.assertRaises(ValueError):
                validate_cases(bad, "registers")

    def test_transfer_inventory_rejects_missing_or_malformed_registers(self):
        good = {
            "own_cold_cases": True,
            "cases": [
                {
                    "name": name,
                    "rows": rows,
                    "fixture": f"original-register_transfers.json.{name}.tsv",
                    "registers": [[address, 0] for address in range(32)],
                }
                for name, rows in TRANSFERS.items()
            ],
        }
        self.assertEqual(len(validate_cases(good, "register_transfers")), 4)
        for value in (
            None,
            [],
            [[0]],
            [[True, 0]] + [[i, 0] for i in range(1, 32)],
            [[i, 256] for i in range(32)],
        ):
            bad = deepcopy(good)
            bad["cases"][0]["registers"] = value
            with self.assertRaises(ValueError):
                validate_cases(bad, "register_transfers")

    def test_complete_requires_exact_observations_and_clean_execution(self):
        good = subprocess.CompletedProcess(
            [], 0, "803 original bus observations; 0 differences\n", ""
        )
        self.assertTrue(complete(good, 803))
        self.assertFalse(complete(good, 802))
        self.assertFalse(complete(subprocess.CompletedProcess([], 1, good.stdout, ""), 803))
        self.assertFalse(complete(subprocess.CompletedProcess([], 0, good.stdout, "warning"), 803))

    def test_mode_inventory_requires_all_values_and_both_readbacks(self):
        good = {
            "own_cold_cases": True,
            "cases": [
                {
                    "mode": mode,
                    "rows": 111,
                    "value": 0,
                    "second": 0,
                    "fixture": f"original-mode_registers.json.{mode}.tsv",
                }
                for mode in range(256)
            ],
        }
        self.assertEqual(len(validate_cases(good, "mode_registers")), 256)
        for mutate in (
            lambda m: m["cases"].pop(),
            lambda m: m["cases"][0].update(mode=True),
            lambda m: m["cases"][0].update(second=256),
            lambda m: m["cases"][0].pop("value"),
        ):
            bad = deepcopy(good)
            mutate(bad)
            with self.assertRaises(ValueError):
                validate_cases(bad, "mode_registers")

    def test_fault_requires_unique_actual_live_header_read(self):
        source = "liveSpecial = headerPointer;"
        self.assertEqual(disconnected_header(source), "liveSpecial = 8'hff;")
        for bad in ("", source * 2):
            with self.assertRaises(ValueError):
                disconnected_header(bad)

    def test_nmi_fault_reconnects_pin_to_writable_status_bit(self):
        source = "assign io_nmi = (((eventNmi || commandNmi) && (! io_warmReset)) && (! nmiGap));"
        self.assertEqual(
            conflated_nmi(source),
            "assign io_nmi = (((eventInterrupt || commandNmi) && (! io_warmReset)) && (! nmiGap));",
        )
        for bad in ("", source * 2, "assign io_nmi = (hostNmi && (! io_warmReset));"):
            with self.assertRaises(ValueError):
                conflated_nmi(bad)


if __name__ == "__main__":
    unittest.main()
