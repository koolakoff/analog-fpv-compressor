"""Regression checks for cut mapping, audio clocks, and safe publication."""

from dataclasses import replace
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from threading import Timer
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from analog_fpv_compressor.models import Analysis, CancelToken, Plan, Settings
from analog_fpv_compressor.processing import ProcessingCancelled, ProcessingError, _publish, _run, build_command, execute, expected_timestamps


def make_plan(source, output, tools, probe=None, timestamps=(0., .1, .5, .6, 1.1, 1.2, 1.3), **selected):
    probe = probe or {"streams": [{"codec_type": "video", "time_base": "1/10", "avg_frame_rate": "10/1",
                                   "width": 320, "height": 240, "color_range": "tv"}]}
    settings = Settings(source, output, threads=2)
    metadata = {"source_fingerprint": {"bytes": source.stat().st_size, "mtime_ns": source.stat().st_mtime_ns}} if source.exists() else {}
    analysis = Analysis(source, probe, timestamps=tuple(timestamps), tools=tools, metadata=metadata)
    resolved = {"codec": "av1", "crf": 45, "bitrate": None, "preset": 8,
                "width": 320, "height": 240, "denoise": None, "deinterlace": False,
                "field_order": "auto", "audio": "remove", "threads": 2, **selected}
    return Plan(settings, analysis, resolved, ((.02, .7), (1.1, 1.4)), (), (), {}, "test")


class MappingTests(unittest.TestCase):
    def test_fractional_boundaries_and_sparse_segment_mapping(self):
        plan = make_plan(Path("source.avi"), Path("out.mkv"), {"ffmpeg": "ffmpeg"})
        actual = expected_timestamps(plan)
        desired = [.08, .48, .58, .68, .78, .88]
        self.assertEqual(len(actual), len(desired))
        for first, second in zip(actual, desired):
            self.assertAlmostEqual(first, second)
        command = build_command(plan, Path("temporary.mkv"))
        graph = command[command.index("-filter_complex") + 1]
        self.assertIn("trim=start_pts=1:end_pts=7", graph)
        self.assertIn("eq(N,3)", graph)
        self.assertNotIn("hqdn3d", graph)

    def test_empty_retained_video_fails(self):
        plan = make_plan(Path("source.avi"), Path("out.mkv"), {})
        with self.assertRaises(ProcessingError):
            expected_timestamps(replace(plan, keep_intervals=()))

    def test_atomic_publication_never_overwrites(self):
        with tempfile.TemporaryDirectory() as directory:
            source, target = Path(directory) / "partial", Path(directory) / "final"
            source.write_bytes(b"new")
            target.write_bytes(b"existing")
            with self.assertRaises(ProcessingError):
                _publish(source, target)
            self.assertEqual(target.read_bytes(), b"existing")
            self.assertTrue(source.exists())

    def test_cancel_stops_running_child(self):
        token = CancelToken()
        timer = Timer(.2, token.cancel)
        timer.start()
        try:
            with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as diagnostic:
                with self.assertRaises(ProcessingCancelled):
                    _run([sys.executable, "-c", "import time; print('running', flush=True); time.sleep(30)"], diagnostic, cancel=token)
        finally:
            timer.cancel()


class FFmpegIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        directory = os.environ.get("FPV_FFMPEG_BIN")
        ffmpeg = str(Path(directory) / "ffmpeg.exe") if directory else shutil.which("ffmpeg")
        ffprobe = str(Path(directory) / "ffprobe.exe") if directory else shutil.which("ffprobe")
        if not ffmpeg or not ffprobe:
            raise unittest.SkipTest("Set FPV_FFMPEG_BIN or install FFmpeg for integration checks")
        cls.tools = {"ffmpeg": ffmpeg, "ffprobe": ffprobe}
        cls.directory = tempfile.TemporaryDirectory()
        cls.source = Path(cls.directory.name) / "Unicode тест source.mkv"
        subprocess.run([ffmpeg, "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=10:duration=3",
                        "-f", "lavfi", "-i", "sine=frequency=500:sample_rate=8000:duration=3", "-vf",
                        "select='not(between(n,2,4))'", "-fps_mode", "passthrough", "-c:v", "ffv1",
                        "-color_range", "tv", "-c:a", "pcm_s16le", str(cls.source)], check=True)
        cls.probe = json.loads(subprocess.check_output([ffprobe, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(cls.source)]))
        frames = json.loads(subprocess.check_output([ffprobe, "-v", "error", "-select_streams", "v:0", "-show_frames",
                                                    "-show_entries", "frame=best_effort_timestamp_time", "-of", "json", str(cls.source)]))
        cls.timestamps = [float(f["best_effort_timestamp_time"]) for f in frames["frames"]]

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, "directory"):
            cls.directory.cleanup()

    def plan(self, name, **selected):
        return make_plan(self.source, Path(self.directory.name) / name, self.tools, self.probe, self.timestamps, **selected)

    def test_sparse_cuts_preserve_clock_and_lossless_audio(self):
        result = execute(self.plan("cut keep.mkv", audio="keep"))
        validation = result.report["validation"]
        self.assertEqual(validation["decoded_frames"], 6)
        self.assertEqual(validation["audio"]["decoded_samples"], 7840)
        self.assertLess(validation["maximum_timestamp_error_seconds"], .0011)
        self.assertTrue(result.output_path.exists())
        self.assertTrue(result.report_path.exists())

    def test_progressive_no_audio_and_no_overwrite(self):
        plan = self.plan("cut silent.mkv")
        result = execute(plan)
        self.assertIsNone(result.report["validation"]["audio"])
        with self.assertRaises(ProcessingError):
            execute(plan)

    def test_cancelled_job_never_publishes(self):
        token = CancelToken()
        token.cancel()
        plan = self.plan("cancelled.mkv")
        with self.assertRaises(ProcessingCancelled):
            execute(plan, cancel=token)
        self.assertFalse(plan.settings.output_path.exists())

    def test_publication_race_preserves_other_output_and_removes_own_report(self):
        plan = self.plan("publication race.mkv")

        def publish_with_race(source, destination):
            if destination == plan.settings.output_path:
                destination.write_bytes(b"another job's output")
            _publish(source, destination)

        with patch("analog_fpv_compressor.processing._publish", side_effect=publish_with_race):
            with self.assertRaises(ProcessingError):
                execute(plan)
        self.assertEqual(plan.settings.output_path.read_bytes(), b"another job's output")
        self.assertFalse(Path(str(plan.settings.output_path) + ".report.json").exists())
        self.assertFalse(list(plan.settings.output_path.parent.glob("*.partial*")))

    def test_changed_source_fingerprint_is_rejected(self):
        plan = self.plan("stale.mkv")
        stale = replace(plan.analysis, metadata={"source_fingerprint": {"bytes": 1, "mtime_ns": 0}})
        with self.assertRaises(ProcessingError):
            execute(replace(plan, analysis=stale))
        self.assertFalse(plan.settings.output_path.exists())

    def test_manual_deinterlace_preserves_both_fields(self):
        result = execute(self.plan("fields.mkv", deinterlace=True))
        self.assertEqual(result.report["validation"]["decoded_frames"], 12)

    def test_mp4_audio_clock(self):
        result = execute(self.plan("cut.mp4", audio="keep"))
        self.assertEqual(result.report["validation"]["decoded_frames"], 6)
        self.assertEqual(result.report["validation"]["audio"]["sample_rate"], 8000)


if __name__ == "__main__":
    unittest.main()
