"""Verify the declared sector timing profile against the pinned original FDC."""

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from tools.bbc import build, disc_probe
from tools.validation.common import ROOT, source_root
from tools.validation.setup import verify


class DiscOracleTests(unittest.TestCase):
    def test_real_controller_write_pipeline_and_readback_reject_corrupted_host_data(self):
        receipt = disc_probe.qualify()
        self.assertEqual(receipt["bus_observations"], 4179)
        self.assertEqual(receipt["physical_payload_bytes"], 256)
        directory = build.BBC / "disc-probe"
        fixture = (directory / "original.json.bus.tsv").read_text().splitlines()
        index = next(i for i, row in enumerate(fixture) if row.split()[1:3] == ["W", "4"])
        values = fixture[index].split()
        values[3] = str(int(values[3]) ^ 1)
        fixture[index] = " ".join(values)
        with tempfile.TemporaryDirectory() as temporary:
            corrupt = Path(temporary) / "corrupt.tsv"
            corrupt.write_text("\n".join(fixture) + "\n")
            result = subprocess.run(
                [
                    str(directory / "VIntel8271"),
                    str(corrupt),
                    str(build.BBC / "disc.ssd"),
                    str(Path(temporary) / "writes.tsv"),
                    "--fail-fast",
                ],
                cwd=build.BBC,
                capture_output=True,
                text=True,
                timeout=120,
            )
        self.assertEqual(
            result.returncode, 1, "A corrupted host WRITE byte must fail original sector readback"
        )
        self.assertIn("differences", result.stdout)

    def test_clocked_transport_preserves_every_cell_on_track_and_side_changes(self):
        verify()
        script = r"""
import assert from 'node:assert/strict';
import {pathToFileURL} from 'node:url';
const [root,fixture,ssd]=process.argv.slice(2);
const {IntelFdc}=await import(pathToFileURL(root+'/src/intel-fdc.js'));
const {Scheduler}=await import(pathToFileURL(root+'/src/scheduler.js'));
const {discFor}=await import(pathToFileURL(root+'/src/fdc.js'));
const {configureDrive}=await import(pathToFileURL(fixture));
const fs=await import('node:fs');
function drive(profile){
 const fdc=new IntelFdc({setNmi(){},nmiAsserted:()=>false},new Scheduler());
 fdc.loadDisc(0,discFor('elite.ssd',new Uint8Array(fs.readFileSync(ssd))));
 configureDrive(fdc,profile);return fdc._drives[0];
}
const original=drive('constant-fm'),clocked=drive('clocked-fm');let lost=0;
for(let cell=0;cell<3125;cell++){
 for(const d of [original,clocked])d.positionFraction=(cell+0.5)/3125;
 const fraction=clocked.positionFraction;
 assert.ok(Math.abs(fraction-cell/3125)<=Number.EPSILON);
 original.seekOneTrack(original.track===0?1:-1);
 if(original.headPosition!==cell)lost++;
 clocked.seekOneTrack(clocked.track===0?1:-1);
 assert.equal(clocked.headPosition,cell,'Seek must preserve cell position');
 clocked.selectSide(false);
 assert.equal(clocked.headPosition,cell,'Side selection must preserve cell position');
 assert.equal(clocked.positionTime,cell*128,'Exact 64 us FM cell clock');
}
assert.equal(lost,172,'Keep evidence of the original fractional round-trip loss');
"""
        subprocess.run(
            [
                "node",
                "--input-type=module",
                "-",
                str(source_root("jsbeeb")),
                str(ROOT / "tools/bbc/reference_drive.mjs"),
                str(source_root("elite_a") / "5-compiled-game-discs/elite-a-released.ssd"),
            ],
            input=script,
            text=True,
            capture_output=True,
            check=True,
            timeout=30,
        )

    def test_original_fdc_payload_sector_gaps_and_crc_completion(self):
        self.qualify_profile("constant-fm")

    def test_clocked_fm_exact_payload_sector_gaps_and_crc_completion(self):
        self.qualify_profile("clocked-fm")

    def qualify_profile(self, profile):
        verify()
        ssd = source_root("elite_a") / "5-compiled-game-discs/elite-a-released.ssd"
        image = ssd.read_bytes().ljust(204800, b"\0")
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "timing.json"
            command = subprocess.run(
                [
                    "node",
                    str(ROOT / "tools/bbc/disc_timing.mjs"),
                    str(source_root("jsbeeb")),
                    str(ssd),
                    str(output),
                    profile,
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(command.returncode, 0, command.stdout + command.stderr)
            result = json.loads(output.read_text())
            cases = result["cases"]
        self.assertEqual(result["profile"], profile)
        self.assertEqual(
            [(c["select"], c["operation"], c["result"]) for c in result["absentDrives"]],
            [(select, op, 0x10) for select in [0x80, 0, 0xC0] for op in [0x13, 0x0B]],
        )
        self.assertEqual(result["coldResult"], 0x10)
        self.assertEqual(len(cases), 5)
        for case in cases:
            offset = case["track"] * 2560 + case["sector"] * 256
            self.assertEqual(bytes(case["data"]), image[offset : offset + case["bytes"]])
            # jsbeeb converts floating track position to integer host cycles;
            # per-cell rounding alternates by one 2 MHz cycle. The RTL fixture
            # checks exact integer 64 us cells and 128 us CRC completion.
            tolerance = 0 if profile == "clocked-fm" else 2
            self.assertTrue(
                all(
                    abs(n - 128) <= tolerance
                    for i, n in enumerate(case["intervals"])
                    if (i + 1) % 256
                )
            )
            self.assertLessEqual(abs(case["completion"]["cycle"] - case["last"] - 256), tolerance)
            if case["count"] == 2:
                self.assertEqual(case["intervals"][255], 55 * 128)
