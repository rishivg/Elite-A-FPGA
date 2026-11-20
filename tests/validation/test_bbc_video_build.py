"""Parallel workers must share one complete, content-checked elaboration."""

import fcntl
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from tools.bbc import video96
from tools.validation.common import ROOT


class VideoBuildTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir=ROOT / "build")
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.addCleanup(patch.stopall)
        patch.object(video96, "DIRECTORY", self.directory).start()
        patch.object(video96, "generation_inputs", return_value={"source": "checked"}).start()

    def write_rtl(self):
        (self.directory / "BbcVideo96.v").write_text("module BbcVideo96; endmodule\n")

    def test_concurrent_workers_elaborate_once(self):
        entered, release, second_attempt = threading.Event(), threading.Event(), threading.Event()
        real_flock = fcntl.flock
        attempts = []

        def lock(descriptor, operation):
            attempts.append(descriptor)
            if len(attempts) == 2:
                second_attempt.set()
            return real_flock(descriptor, operation)

        def elaborate(*args, **kwargs):
            entered.set()
            if not release.wait(10):
                raise RuntimeError("Test did not release the first elaboration")
            self.write_rtl()

        with (
            patch.object(video96.fcntl, "flock", side_effect=lock),
            patch.object(video96.subprocess, "run", side_effect=elaborate) as compiler,
            ThreadPoolExecutor(max_workers=2) as executor,
        ):
            first = executor.submit(video96.generate)
            try:
                self.assertTrue(entered.wait(10))
                second = executor.submit(video96.generate)
                self.assertTrue(second_attempt.wait(10))
                self.assertFalse(second.done())
                self.assertEqual(compiler.call_count, 1)
            finally:
                release.set()
            self.assertEqual(first.result(timeout=10), second.result(timeout=10))
            self.assertEqual(compiler.call_count, 1)

    def test_failed_elaboration_releases_lock_without_valid_receipt(self):
        with (
            patch.object(video96.subprocess, "run", side_effect=RuntimeError("failed")),
            self.assertRaisesRegex(RuntimeError, "failed"),
        ):
            video96.generate()
        self.assertFalse((self.directory / "elaboration.json").exists())
        with patch.object(video96.subprocess, "run", side_effect=lambda *a, **k: self.write_rtl()):
            state = video96.generate()
        self.assertTrue(state["outputs"])


class VideoComponentCacheTests(unittest.TestCase):
    def test_unrelated_machine_changes_reuse_video_but_rom_changes_rebuild(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "build") as temporary:
            directory = Path(temporary)
            model = directory / "BbcVideo96.v"
            model.write_text('module BbcVideo96; initial $readmemb("font.bin", font); endmodule\n')
            rom = directory / "font.bin"
            rom.write_text("1010\n")
            machine = directory / "BbcMachine96.v"
            machine.write_text("module BbcMachine96; endmodule\n")
            cpp = directory / "probe.cpp"
            cpp.write_text("// native probe\n")

            def compile_model(*args, **kwargs):
                (directory / "probe/VBbcVideo96").write_bytes(b"compiled model")

            with (
                patch.object(video96, "DIRECTORY", directory),
                patch.object(video96, "generation_inputs", return_value={"source": "checked"}),
                patch.object(video96, "generate", side_effect=video96.snapshot),
                patch.object(video96.subprocess, "check_output", return_value="test compiler"),
                patch.object(video96.subprocess, "run", side_effect=compile_model) as compiler,
            ):
                video96.compile("probe", cpp)
                self.assertEqual(compiler.call_count, 1)
                machine.write_text("module BbcMachine96; wire changed; endmodule\n")
                video96.compile("probe", cpp)
                self.assertEqual(compiler.call_count, 1)
                rom.write_text("1011\n")
                video96.compile("probe", cpp)
                self.assertEqual(compiler.call_count, 2)

    def test_referenced_rom_must_exist_inside_generated_directory(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "build") as temporary:
            directory = Path(temporary)
            model = directory / "BbcVideo96.v"
            with patch.object(video96, "DIRECTORY", directory):
                for name in ("missing.bin", "../outside.bin"):
                    model.write_text(
                        f'module BbcVideo96; initial $readmemh("{name}", font); endmodule\n'
                    )
                    with self.assertRaisesRegex(ValueError, "Missing or external"):
                        video96.component_snapshot()


if __name__ == "__main__":
    unittest.main()
