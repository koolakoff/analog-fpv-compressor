"""Real CLI rejection and live FFmpeg cancellation regression checks."""

import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from threading import Timer
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from analog_fpv_compressor import controller, processing
from analog_fpv_compressor.models import CancelToken, Settings


class FailureIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        directory = os.environ.get("FPV_FFMPEG_BIN")
        cls.ffmpeg = str(Path(directory) / "ffmpeg.exe") if directory else shutil.which("ffmpeg")
        cls.ffprobe = str(Path(directory) / "ffprobe.exe") if directory else shutil.which("ffprobe")
        if not cls.ffmpeg or not cls.ffprobe:
            raise unittest.SkipTest("Set FPV_FFMPEG_BIN or install FFmpeg to run integration checks")
        cls.ffmpeg_dir = Path(cls.ffmpeg).parent

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)

    def tearDown(self):
        self.directory.cleanup()

    def generate(self, output, *arguments):
        subprocess.run([self.ffmpeg, "-v", "error", "-threads", "2", *map(str, arguments), str(output)], check=True)

    def cli(self, source, output, *options):
        env = dict(os.environ)
        env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
        return subprocess.run([sys.executable, "-m", "analog_fpv_compressor", "--input", str(source),
                               "--output", str(output), "--ffmpeg-dir", str(self.ffmpeg_dir), "--threads", "2",
                               *options], capture_output=True, text=True, encoding="utf-8", env=env, timeout=30)

    def assert_unpublished(self, output):
        self.assertFalse(output.exists())
        self.assertFalse(Path(str(output) + ".report.json").exists())
        self.assertFalse(any(".partial" in path.name for path in self.root.iterdir()))

    def test_entire_snow_default_rejects_and_explicit_off_succeeds(self):
        source = self.root / "only snow.AVI"
        self.generate(source, "-f", "lavfi", "-i",
                      "nullsrc=size=160x120:rate=30:duration=3,geq=lum='random(1)*255':cb=128:cr=128,"
                      "scale=640:480:flags=neighbor:out_range=full", "-an", "-c:v", "mjpeg",
                      "-pix_fmt", "yuvj422p", "-color_range", "pc", "-q:v", "3")
        output = self.root / "rejected.mkv"
        rejected = self.cli(source, output)
        self.assertNotEqual(rejected.returncode, 0, rejected.stderr)
        self.assertIn("only snow", rejected.stderr.lower())
        self.assert_unpublished(output)
        output = self.root / "explicit keep.mkv"
        kept = self.cli(source, output, "--cut-no-signal", "off", "--preset", "8")
        self.assertEqual(kept.returncode, 0, kept.stderr)
        self.assertTrue(output.exists())
        self.assertTrue(Path(str(output) + ".report.json").exists())

    def test_empty_and_corrupt_inputs_are_rejected(self):
        for label, content in (("empty", b""), ("corrupt", b"RIFF\x80\x00\x00\x00AVI LISTbroken-media-data")):
            source, output = self.root / f"{label}.AVI", self.root / f"{label}.mkv"
            source.write_bytes(content)
            rejected = self.cli(source, output)
            self.assertNotEqual(rejected.returncode, 0, rejected.stderr)
            self.assert_unpublished(output)

    def test_live_encode_cancellation_reaps_child_and_removes_partial_files(self):
        source, output = self.root / "useful.mkv", self.root / "cancelled.mkv"
        self.generate(source, "-f", "lavfi", "-i", "testsrc2=size=720x480:rate=30:duration=8",
                      "-an", "-c:v", "ffv1", "-color_range", "tv")
        settings = Settings(source, output, cut_no_signal="off", deinterlace="off", denoise="off",
                            preset=4, threads=1, ffmpeg_dir=self.ffmpeg_dir)
        plan = controller.build_plan(settings, controller.analyze(settings))
        token, children, timers = CancelToken(), [], []
        original_popen = subprocess.Popen

        def observe_real_child(*args, **kwargs):
            # Instrument actual Popen objects; no fake process or success is used.
            child = original_popen(*args, **kwargs)
            children.append(child)
            return child

        def events(event):
            if event.code == "processing_started":
                timer = Timer(.2, token.cancel)
                timers.append(timer)
                timer.start()

        started = time.perf_counter()
        try:
            with patch.object(processing.subprocess, "Popen", side_effect=observe_real_child):
                with self.assertRaises(processing.ProcessingCancelled):
                    processing.execute(plan, emit=events, cancel=token)
        finally:
            for timer in timers:
                timer.cancel()
        self.assertTrue(children, "Cancellation must occur after starting a real FFmpeg child")
        self.assertTrue(all(child.poll() is not None for child in children))
        self.assertLess(time.perf_counter() - started, 5)
        self.assert_unpublished(output)


if __name__ == "__main__":
    unittest.main()
