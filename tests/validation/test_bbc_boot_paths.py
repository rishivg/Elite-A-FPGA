"""Boot backends use different working directories but one output directory."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.bbc import boot
from tools.validation.common import ROOT


class BootPathsTests(unittest.TestCase):
    def test_relative_output_has_the_same_absolute_location_for_both_backends(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "build") as temporary:
            root = Path(temporary)
            output = (root / "captures").relative_to(ROOT)
            states = {name: [root / (name + ".state")] for name in ["dut", "reference"]}
            for paths in states.values():
                paths[0].write_bytes(b"own state")
            receipt = root / "boot-cache.json"
            receipt.write_text('{"schema":2,"backends":{}}')
            requests = []

            def execute(command, cwd, log, *args, **kwargs):
                requests.append((command, cwd))

            with (
                patch.object(boot.boot_cache, "backends", return_value=states),
                patch.object(boot.boot_cache, "valid", return_value=False),
                patch.object(boot.boot_cache, "inputs", return_value={"test": "inputs"}),
                patch.object(boot.boot_cache, "seal"),
                patch.object(boot.boot_cache, "receipt_path", return_value=receipt),
                patch.object(boot, "run", side_effect=execute),
                patch.object(boot, "source_root", return_value=root / "reference-source"),
            ):
                boot.prepare(output, {"reference_cpu": "cycle"})
            dut_command, dut_cwd = next(r for r in requests if r[0][0].endswith("VBbcMachine"))
            reference_command, reference_cwd = next(r for r in requests if r[0][0] == "node")
            reference = json.loads(Path(reference_command[2]).read_text())
            self.assertNotEqual(dut_cwd, reference_cwd)
            self.assertEqual(Path(dut_command[-1]), root / "captures/boot-dut")
            self.assertEqual(Path(reference["output"]), root / "captures/boot-reference")


if __name__ == "__main__":
    unittest.main()
