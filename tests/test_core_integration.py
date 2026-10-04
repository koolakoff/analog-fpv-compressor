"""Synthetic content-level audio sync and automatic field classification checks."""

from dataclasses import replace
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import wave

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from analog_fpv_compressor import controller, processing
from analog_fpv_compressor.models import Settings


class CoreIntegrationTests(unittest.TestCase):
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

    def run_ffmpeg(self, *arguments):
        return subprocess.check_output([self.ffmpeg, "-v", "error", "-threads", "2", *map(str, arguments)])

    def settings(self, source, output, **options):
        return Settings(source, output, cut_no_signal="off", deinterlace="off", denoise="off",
                        crf=20, preset=8, threads=2, ffmpeg_dir=self.ffmpeg_dir, **options)

    def test_audio_content_and_video_events_share_the_interval_clock(self):
        # Audio begins at original t=0.1, so the first retained interval starting
        # at t=0.02 requires exactly 80 ms of silence before original samples.
        samples = np.zeros(24000, dtype="<i2")
        samples[400:480] = 12000
        samples[5000:5080] = 18000  # This pulse falls inside the removed region.
        samples[8400:8560] = -14000
        audio_path = self.root / "pulses.wav"
        with wave.open(str(audio_path), "wb") as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(8000)
            audio.writeframes(samples.tobytes())
        source = self.root / "offset events.mkv"
        self.run_ffmpeg("-f", "lavfi", "-i", "testsrc2=size=320x240:rate=10:duration=3",
                        "-itsoffset", "0.1", "-i", audio_path, "-vf",
                        "drawbox=x=140:y=100:w=40:h=40:color=red:t=fill:enable='between(t,.1,.19)',"
                        "drawbox=x=140:y=100:w=40:h=40:color=blue:t=fill:enable='between(t,1.1,1.19)',"
                        "select='not(eq(n,0)+between(n,2,4))'", "-fps_mode", "passthrough",
                        "-c:v", "ffv1", "-color_range", "tv", "-c:a", "pcm_s16le", source)
        settings = self.settings(source, self.root / "kept.mkv", audio="keep")
        analysis = controller.analyze(settings)
        plan = controller.build_plan(settings, analysis)
        intervals = ((.02, .7), (1.1, 1.4))
        plan = replace(plan, keep_intervals=intervals,
                       mapping=({"source_start": .02, "source_end": .7, "output_start": 0., "output_end": .68},
                                {"source_start": 1.1, "source_end": 1.4, "output_start": .68, "output_end": .98}))
        result = processing.execute(plan)
        decoded = self.run_ffmpeg("-i", result.output_path, "-map", "0:a:0", "-f", "s16le", "-c:a", "pcm_s16le", "-")
        actual = np.frombuffer(decoded, dtype="<i2")
        expected = np.concatenate((np.zeros(640, dtype="<i2"), samples[:4800], samples[8000:10400]))
        np.testing.assert_array_equal(actual, expected)
        self.assertTrue(np.all(actual[:640] == 0))
        self.assertFalse(np.any(actual == 18000))
        # The red and blue video events lead their corresponding pulses by the
        # same 50 ms after cutting, checked independently of waveform counts.
        rgb = self.run_ffmpeg("-i", result.output_path, "-map", "0:v:0", "-an", "-fps_mode", "passthrough",
                              "-pix_fmt", "rgb24", "-f", "rawvideo", "-")
        frames = np.frombuffer(rgb, dtype=np.uint8).reshape(-1, 240, 320, 3)
        self.assertEqual(len(frames), 6)
        red = frames[0, 112:128, 152:168].mean(axis=(0, 1))
        blue = frames[3, 112:128, 152:168].mean(axis=(0, 1))
        self.assertGreater(red[0], red[1] + 100)
        self.assertGreater(blue[2], blue[0] + 100)
        raw = subprocess.check_output([self.ffprobe, "-v", "error", "-select_streams", "v:0", "-show_frames",
                                       "-show_entries", "frame=best_effort_timestamp_time", "-of", "json", str(result.output_path)])
        times = [float(f["best_effort_timestamp_time"]) for f in json.loads(raw)["frames"]]
        self.assertAlmostEqual(1040 / 8000 - times[0], .05, places=3)
        self.assertAlmostEqual(5840 / 8000 - times[3], .05, places=3)

    def test_auto_idet_distinguishes_progressive_and_merged_fields(self):
        progressive = self.root / "progressive.mkv"
        interlaced = self.root / "merged fields.mkv"
        self.run_ffmpeg("-f", "lavfi", "-i", "testsrc2=size=320x240:rate=30:duration=6",
                        "-vf", "scale=320:480:flags=bilinear", "-c:v", "ffv1", "-color_range", "tv", progressive)
        self.run_ffmpeg("-f", "lavfi", "-i", "testsrc2=size=320x240:rate=60:duration=6",
                        "-vf", "tinterlace=mode=merge", "-c:v", "ffv1", "-color_range", "tv", interlaced)
        for source, expected in ((progressive, "progressive"), (interlaced, "interlaced")):
            settings = replace(self.settings(source, self.root / f"{expected}-out.mkv"), deinterlace="auto")
            analysis = controller.analyze(settings)
            self.assertEqual(analysis.interlace["decision"], expected, analysis.interlace)
            plan = controller.build_plan(settings, analysis)
            self.assertEqual(plan.selected["deinterlace"], expected == "interlaced")
            if expected == "interlaced":
                self.assertEqual(analysis.interlace["field_order"], "tff")
                result = processing.execute(plan)
                self.assertEqual(result.report["validation"]["decoded_frames"], 360)

    def test_mixed_field_evidence_preserves_known_progressive_frames(self):
        source = self.root / "aliased progressive.mkv"
        self.run_ffmpeg("-f", "lavfi", "-i", "testsrc2=size=320x240:rate=30:duration=6",
                        "-c:v", "ffv1", "-color_range", "tv", source)
        settings = replace(self.settings(source, self.root / "preserved.mkv"), deinterlace="auto")
        analysis = controller.analyze(settings)
        self.assertEqual(analysis.interlace["decision"], "unknown", analysis.interlace)
        self.assertLess(analysis.interlace["parity_confidence"], .8)
        self.assertFalse(controller.build_plan(settings, analysis).selected["deinterlace"])


if __name__ == "__main__":
    unittest.main()
