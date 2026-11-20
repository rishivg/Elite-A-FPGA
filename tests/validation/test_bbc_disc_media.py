"""Writable-media evidence must include complete histories and actual flux."""

import subprocess
import unittest
from copy import deepcopy

from tools.bbc.disc_media import (
    CASES,
    CRC_CASES,
    STORAGE_CELLS,
    complete,
    ignore_marker_damage,
    raw_writes,
    validate_cases,
)


class DiscMediaTests(unittest.TestCase):
    def metadata(self):
        cases = []
        for (name, rows, transfers), cells in zip(CASES, STORAGE_CELLS, strict=True):
            flux = [[i, 3, 667 + i % 256, i % 256] for i in range(cells)]
            cases.append(
                {
                    "name": name,
                    "rows": rows,
                    "fixture": f"original.json.{name}.tsv",
                    "transfers": [{"bytes": b, "result": r} for b, r in transfers],
                    "flux": flux,
                    "rawFlux": [row + [255] for row in flux],
                    "storageWrites": [
                        [epoch, 8192 + cell - 667, value] for epoch, _, cell, value in flux
                    ],
                }
            )
        return {"own_cold_cases": True, "cases": cases}

    def test_inventory_rejects_missing_cases_and_incomplete_physical_writes(self):
        good = self.metadata()
        self.assertEqual(len(validate_cases(good)), 17)
        for mutate in (
            lambda m: m["cases"].pop(),
            lambda m: m["cases"].reverse(),
            lambda m: m.update(own_cold_cases=False),
            lambda m: m["cases"][0].update(fixture="../other.tsv"),
            lambda m: m["cases"][0].update(flux=[], storageWrites=[]),
            lambda m: m["cases"][0].update(rawFlux=[]),
            lambda m: m["cases"][0]["storageWrites"][0].__setitem__(2, 99),
        ):
            bad = deepcopy(good)
            mutate(bad)
            with self.assertRaises(ValueError):
                validate_cases(bad)

    def test_same_cell_same_time_abort_uses_final_physical_value(self):
        good = self.metadata()
        case = good["cases"][10]
        case["flux"] = [[1285577, 3, 667, 3], [1285577, 3, 667, 255]]
        case["rawFlux"] = [row + [255] for row in case["flux"]]
        case["storageWrites"] = [[1285577, 8192, 255]]
        validate_cases(good)
        case["flux"][1][0] += 1
        with self.assertRaises(ValueError):
            validate_cases(good)

    def test_completion_and_fault_target_are_strict(self):
        good = subprocess.CompletedProcess(
            [], 0, "147 original bus observations; 0 differences\n", ""
        )
        self.assertTrue(complete(good, 147))
        self.assertFalse(complete(good, 148))
        self.assertFalse(complete(subprocess.CompletedProcess([], 2, good.stdout, ""), 147))
        source = "assign validDataMarker = (clocksOk && markerOk);"
        self.assertEqual(ignore_marker_damage(source), "assign validDataMarker = 1'b1;")
        for bad in ("", source * 2):
            with self.assertRaises(ValueError):
                ignore_marker_damage(bad)

    def test_crc_boundaries_require_all_six_histories_and_correct_recovery(self):
        flux = [[i, 3, 667 + i, i] for i in range(256)]
        good = {
            "own_cold_cases": True,
            "cases": [
                {
                    "name": name,
                    "rows": rows,
                    "fixture": f"original-crc.json.{name}.tsv",
                    "transfers": [{"bytes": b, "result": r} for b, r in transfers],
                    "flux": flux,
                    "rawFlux": [row + [255] for row in flux],
                    "storageWrites": [[i, 8192 + i, i] for i in range(256)],
                }
                for name, rows, transfers in CRC_CASES
            ],
        }
        self.assertEqual(len(validate_cases(good, crc=True)), 6)
        bad = deepcopy(good)
        bad["cases"].pop()
        with self.assertRaises(ValueError):
            validate_cases(bad, crc=True)
        bad = deepcopy(good)
        bad["cases"][4]["transfers"][1]["result"] = 14
        with self.assertRaises(ValueError):
            validate_cases(bad, crc=True)

    def test_raw_words_retain_clocks_and_all_track_cells(self):
        flux = [[128, 0, 22, 254, 199], [256, 3, 666, 251, 199], [256, 3, 666, 255, 255]]
        self.assertEqual(raw_writes(flux), [[128, 22, 0xC7FE], [256, 10041, 0xFFFF]])
        changed = deepcopy(flux)
        changed[0][4] ^= 1
        self.assertNotEqual(raw_writes(flux), raw_writes(changed))
        self.assertEqual(raw_writes([]), [])
        for bad in (
            None,
            [[0, 0, 3125, 0, 255]],
            [[0, 80, 0, 0, 255]],
            [[0, 0, 0, 256, 255]],
            [[0, 0, 0, 0, True]],
            [[0, 0, 0, 0]],
        ):
            with self.assertRaises(ValueError):
                raw_writes(bad)


if __name__ == "__main__":
    unittest.main()
