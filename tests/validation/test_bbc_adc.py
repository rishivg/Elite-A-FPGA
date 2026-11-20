"""Original ADC/VIA conversion and completion-interrupt qualification."""

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.bbc import adc_bus, adc_results
from tools.bbc.adc_probe import qualify
from tools.validation.common import ROOT, source_root


class AdcTests(unittest.TestCase):
    def test_conversion_restart_completion_interrupts_and_acknowledgement(self):
        result = qualify()
        self.assertTrue(result["passed"])
        self.assertEqual(result["cases"], 112)
        self.assertEqual(result["observations"], 2000)

    def test_nec_busy_retention_aliases_and_high_byte_acknowledgement(self):
        result = adc_bus.prepare()
        self.assertTrue(result["passed"])
        self.assertEqual(result["cases"], 256)
        self.assertEqual(result["observations"], 4352)

    def test_nec_authority_changes_invalidate_bus_qualification(self):
        adc_bus.prepare()
        with tempfile.TemporaryDirectory(dir=ROOT / "build") as temporary:
            changed = Path(temporary) / "changed.pdf"
            changed.write_bytes(adc_bus.DATASHEET.read_bytes() + b"\n")
            with patch.object(adc_bus, "DATASHEET", changed):
                self.assertFalse(adc_bus.current())

    def test_complete_domain_against_independent_c_model(self):
        result = adc_results.prepare()
        self.assertEqual(result["rtl"]["cases"], 1048576)
        self.assertEqual(result["rtl"]["differences"], 0)
        self.assertEqual(result["reference"]["profiles"]["bbc-result-bits"]["differences"], 0)
        self.assertGreater(result["reference"]["profiles"]["original"]["differences"], 0)
        self.assertTrue(adc_results.current())

    def test_truncated_corpus_and_incomplete_receipt_cannot_be_reused(self):
        result = adc_results.prepare()
        with tempfile.TemporaryDirectory(dir=ROOT / "build") as temporary:
            directory = Path(temporary)
            corpus = directory / "truncated.bin"
            corpus.write_bytes(adc_results.CORPUS.read_bytes()[:-1])
            with patch.object(adc_results, "CORPUS", corpus):
                self.assertFalse(adc_results.current())
            receipt = directory / "receipt.json"
            result["rtl"]["cases"] -= 1
            receipt.write_text(json.dumps(result))
            with patch.object(adc_results, "RECEIPT", receipt):
                self.assertFalse(adc_results.current())

    def test_actual_runtime_rejects_an_original_adc_snapshot(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "build") as temporary:
            directory = Path(temporary)
            saved = directory / "original.state"
            subprocess.run(
                [
                    "node",
                    "--input-type=module",
                    "-e",
                    "import fs from 'node:fs'; import {serialize} from 'node:v8'; "
                    "fs.writeFileSync(process.argv[1],serialize({adcProfile:'original'}));",
                    str(saved),
                ],
                check=True,
                capture_output=True,
                timeout=30,
            )
            config = directory / "config.json"
            config.write_text(
                json.dumps(
                    {
                        "jsbeeb": str(source_root("jsbeeb")),
                        "disc": str(ROOT / "build/bbc/disc.ssd"),
                        "load_state": str(saved),
                        "adc_profile": "bbc-result-bits",
                        "output": str(directory / "capture"),
                    }
                )
            )
            result = subprocess.run(
                ["node", str(ROOT / "tools/bbc/reference.mjs"), str(config)],
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("different CPU/drive/power/ADC profile", result.stderr)
            self.assertFalse((directory / "capture").exists())


if __name__ == "__main__":
    unittest.main()
