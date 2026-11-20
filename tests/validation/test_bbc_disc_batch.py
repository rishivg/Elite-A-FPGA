"""A failed cached batch must not reuse successful observations from an earlier run."""

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.bbc import disc_batch


class DiscBatchTests(unittest.TestCase):
    def test_failed_batch_removes_old_logs_and_storage_writes(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            cases = [
                {"name": name, "rows": 100, "fixture": name + ".tsv"} for name in ("one", "two")
            ]
            for case in cases:
                (directory / case["fixture"]).write_text("0 R 0 0 0\n" * 100)
                (directory / (case["name"] + ".log")).write_text(
                    "100 original bus observations; 0 differences\n"
                )
                (directory / (case["name"] + ".writes.tsv")).write_text("1 8192 3\n")
                (directory / (case["name"] + ".raw.tsv")).write_text("1 10042 65283\n")
            failed = subprocess.CompletedProcess([], 2, "", "Invalid cached state\n")
            with patch.object(disc_batch.subprocess, "run", return_value=failed):
                results, _, reuse = disc_batch.run(directory, cases)
            self.assertEqual(reuse["returncode"], 2)
            for _, result, writes in results:
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, "")
                self.assertFalse(writes.exists())
                self.assertFalse(
                    (directory / (writes.stem.removesuffix(".writes") + ".raw.tsv")).exists()
                )

    def test_duplicate_or_escaping_output_names_are_rejected(self):
        for names in ([], ["same", "same"], ["../elsewhere"], ["a/b"]):
            with self.assertRaises(ValueError):
                disc_batch.run(Path("."), [{"name": name} for name in names])
