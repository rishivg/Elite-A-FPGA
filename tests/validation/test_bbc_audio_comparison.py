"""Long audio comparisons retain every sample, boundary and malformed-capture check."""

import gzip
import subprocess
import tempfile
import unittest
from pathlib import Path

import numpy as np

from tools.bbc.compare import captured_audio
from tools.validation.common import ROOT


class AudioComparisonTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "reference").mkdir()
        (self.root / "dut").mkdir()

    def recordings(self, samples, reference_samples=None):
        reference_samples = samples if reference_samples is None else reference_samples
        (self.root / "reference/audio.f32").write_bytes(bytes(reference_samples * 4))
        (self.root / "reference/audio-digital.u8").write_bytes(bytes(reference_samples))
        (self.root / "dut/audio.u8").write_bytes(bytes(samples))
        return {"duration_us": samples * 2}, {"duration_cycles": samples * 4}

    def test_mismatches_across_chunk_boundary_keep_total_and_first_absolute_offset(self):
        boundary = 1 << 20
        scenario, summary = self.recordings(boundary + 7)
        self.assertTrue(captured_audio(self.root, scenario, summary)["digital_waveform"]["passed"])
        path = self.root / "dut/audio.u8"
        data = bytearray(path.read_bytes())
        for index in [boundary - 1, boundary, boundary + 6]:
            data[index] = 63
        path.write_bytes(data)
        result = captured_audio(self.root, scenario, summary)["digital_waveform"]
        self.assertFalse(result["passed"])
        self.assertEqual(result["different_samples"], 3)
        self.assertEqual(result["first_difference_sample"], boundary - 1)

    def test_nonfinite_float_after_first_chunk_cannot_pass_matching_digital_audio(self):
        boundary = 1 << 20
        scenario, summary = self.recordings(boundary + 1)
        path = self.root / "reference/audio.f32"
        for value in [np.nan, np.inf, -np.inf]:
            with self.subTest(value=value), path.open("r+b") as stream:
                stream.seek(boundary * 4)
                stream.write(np.asarray([value], dtype="<f4").tobytes())
            with self.assertRaisesRegex(ValueError, "invalid audio"):
                captured_audio(self.root, scenario, summary)

    def test_reference_one_sample_short_or_long_keeps_length_failure(self):
        for delta in [-1, 1]:
            with self.subTest(delta=delta):
                scenario, summary = self.recordings(100, 100 + delta)
                result = captured_audio(self.root, scenario, summary)["digital_waveform"]
                self.assertFalse(result["passed"])
                self.assertEqual(result["different_samples"], 1)
                self.assertEqual(result["first_difference_sample"], min(100, 100 + delta))

    def test_partial_float_sample_is_rejected_even_when_integer_counts_match(self):
        scenario, summary = self.recordings(100)
        path = self.root / "reference/audio.f32"
        for trailing in [b"\0", b"\0\0", b"\0\0\0"]:
            with self.subTest(trailing=len(trailing)):
                path.write_bytes(bytes(400) + trailing)
                with self.assertRaisesRegex(ValueError, "invalid audio"):
                    captured_audio(self.root, scenario, summary)

    def compress(self, summary):
        path = self.root / "reference/audio.f32"
        data = path.read_bytes()
        (self.root / "reference/audio.f32.gz").write_bytes(gzip.compress(data, mtime=0))
        path.unlink()
        summary["audio"] = {"float_encoding": "gzip-members", "float_bytes": len(data)}

    def test_lossless_compressed_floats_retain_boundary_and_corruption_checks(self):
        scenario, summary = self.recordings((1 << 20) + 3)
        self.compress(summary)
        self.assertTrue(captured_audio(self.root, scenario, summary)["digital_waveform"]["passed"])
        path = self.root / "reference/audio.f32.gz"
        original = path.read_bytes()
        for broken in [original[:-8], original + gzip.compress(bytes(4), mtime=0)]:
            with self.subTest(bytes=len(broken)):
                path.write_bytes(broken)
                with self.assertRaisesRegex(ValueError, "invalid .*audio"):
                    captured_audio(self.root, scenario, summary)
        data = bytearray(gzip.decompress(original))
        data[-4:] = np.asarray([np.nan], dtype="<f4").tobytes()
        path.write_bytes(gzip.compress(data, mtime=0))
        with self.assertRaisesRegex(ValueError, "invalid audio"):
            captured_audio(self.root, scenario, summary)
        path.write_bytes(original)
        summary["audio"]["float_bytes"] -= 4
        with self.assertRaises(ValueError):
            captured_audio(self.root, scenario, summary)

    def test_streaming_recorder_preserves_bytes_across_gzip_member_boundaries(self):
        script = r"""
import assert from 'node:assert/strict';import fs from 'node:fs';
import {gunzipSync} from 'node:zlib';import {pathToFileURL} from 'node:url';
const [module,root]=process.argv.slice(2);const {BufferedCapture}=await import(pathToFileURL(module));
const data=Buffer.alloc((1<<20)*3+17);for(let i=0;i<data.length;i++)data[i]=(i*17+(i>>>8))&255;
for(const compressed of [false,true]) {
 const file=root+'/'+(compressed?'compressed.gz':'raw');const sink=new BufferedCapture(file,compressed);
 let offset=0;for(const length of [3,8191,1048577,17,1048576,data.length]) {
  const end=Math.min(data.length,offset+length);sink.append(data.subarray(offset,end));offset=end;
 }
 sink.close();assert.equal(sink.bytes,data.length);
 const bytes=fs.readFileSync(file);assert.deepEqual(compressed?gunzipSync(bytes):bytes,data);
 assert.throws(()=>sink.append(Buffer.from([1])),/closed/);
}
const empty=new BufferedCapture(root+'/empty.gz',true);empty.close();
assert.equal(gunzipSync(fs.readFileSync(root+'/empty.gz')).length,0);
"""
        subprocess.run(
            [
                "node",
                "--input-type=module",
                "-",
                str(ROOT / "tools/bbc/reference_capture.mjs"),
                str(self.root),
            ],
            input=script,
            text=True,
            capture_output=True,
            check=True,
            timeout=30,
        )


if __name__ == "__main__":
    unittest.main()
