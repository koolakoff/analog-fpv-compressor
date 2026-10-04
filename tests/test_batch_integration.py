"""Exercise actual wildcard batches and snow-based flight outputs through CLI."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from analog_fpv_compressor.runtime import discover_tools


class BatchIntegrationTests(unittest.TestCase):
    def setUp(self):
        try:
            self.tools = discover_tools(os.environ.get("FPV_FFMPEG_BIN"))
        except ValueError as error:
            self.skipTest(str(error))
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def ffmpeg(self, *arguments):
        subprocess.run([self.tools["ffmpeg"], "-v", "error", "-y", *map(str, arguments)],
                       check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def cli(self, *arguments):
        return subprocess.run([sys.executable, "-m", "analog_fpv_compressor", *map(str, arguments),
                               "--ffmpeg-dir", str(Path(self.tools["ffmpeg"]).parent),
                               "--preset", "10", "--threads", "2"], cwd=self.root,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)

    def test_wildcard_two_files_without_output_and_custom_mp4_directory(self):
        one = self.root / "flight one.mkv"
        self.ffmpeg("-f", "lavfi", "-i", "testsrc2=size=160x120:rate=10:duration=1", "-c:v", "ffv1", one)
        two = self.root / "flight two.mkv"
        two.write_bytes(one.read_bytes())
        result = self.cli("-i", "flight *.mkv", "--cut-no-signal", "off", "--deinterlace", "off")
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
        for source in (one, two):
            output = source.with_name(source.stem + "_converted.mkv")
            report = json.loads(Path(str(output) + ".report.json").read_text())
            self.assertEqual(report["validation"]["decoded_frames"], 10)
        result = self.cli("-i", one, two, "--output-dir", "new outputs", "--output-suffix", "_small",
                          "--format", "mp4", "--cut-no-signal", "off", "--deinterlace", "off")
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
        self.assertTrue((self.root / "new outputs/flight one_small.mp4").is_file())
        self.assertTrue((self.root / "new outputs/flight two_small.mp4").is_file())

    def snow_recording(self):
        source = self.root / "three flights.mkv"
        scene = "testsrc2=size=160x120:rate=10:duration=1"
        snow = "color=c=gray:size=160x120:rate=10:duration=2,noise=alls=100:allf=t+u"
        graph = f"{scene}[a];{snow}[b];{scene}[c];{snow}[d];{scene}[e];[a][b][c][d][e]concat=n=5:v=1:a=0"
        self.ffmpeg("-f", "lavfi", "-i", graph, "-f", "lavfi", "-i",
                    "sine=frequency=500:sample_rate=8000:duration=7", "-c:v", "ffv1", "-c:a", "pcm_s16le", source)
        return source

    def test_three_flights_are_independent_numbered_outputs_with_audio(self):
        source = self.snow_recording()
        result = self.cli("-i", source, "--split-flights", "--output-dir", "flights",
                          "--audio", "keep", "--denoise", "off", "--deinterlace", "off")
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
        summary_path = self.root / "flights/three flights_converted.mkv.report.json"
        summary = json.loads(summary_path.read_text())
        self.assertEqual(summary["status"], "complete")
        self.assertEqual(summary["completed_outputs"], 3)
        self.assertFalse((self.root / "flights/three flights_converted.mkv").exists())
        total_frames = 0
        for index, item in enumerate(summary["outputs"], 1):
            output = Path(item["output_path"])
            self.assertEqual(output.name, f"three flights_converted_{index}.mkv")
            report = json.loads(Path(item["report_path"]).read_text())
            self.assertTrue(report["validation"]["full_decode_passed"])
            self.assertEqual(len(report["plan"]["keep_intervals"]), 1)
            self.assertEqual(report["plan"]["mapping"][0]["output_start"], 0.)
            self.assertEqual(report["validation"]["audio"]["sample_rate"], 8000)
            self.assertEqual(report["validation"]["audio"]["maximum_sample_clock_error_seconds"], 0.)
            total_frames += report["validation"]["decoded_frames"]
        source_times = summary["plan"]["analysis"]["timestamps"]
        retained = sum(any(start <= time < end for start, end in summary["plan"]["keep_intervals"])
                       for time in source_times)
        self.assertEqual(total_frames, retained)

    def test_split_analysis_only_and_collision_create_no_movies(self):
        source = self.snow_recording()
        output = self.root / "preview.mkv"
        result = self.cli("-i", source, "-o", output, "--split-flights", "--analyze-only", "--deinterlace", "off")
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
        summary = json.loads(Path(str(output) + ".report.json").read_text())
        self.assertEqual(summary["status"], "analyzed")
        self.assertEqual(len(summary["outputs"]), 3)
        self.assertFalse(list(self.root.glob("preview*.mkv")))
        existing = self.root / "collision_2.mkv"
        existing.write_bytes(b"keep")
        result = self.cli("-i", source, "-o", self.root / "collision.mkv", "--split-flights", "--deinterlace", "off")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(existing.read_bytes(), b"keep")
        self.assertFalse((self.root / "collision_1.mkv").exists())

    def test_long_adpcm_to_aac_resampling_uses_output_clock_after_trimming(self):
        source = self.root / "unusual-rate.AVI"
        self.ffmpeg("-f", "lavfi", "-i", "testsrc2=size=160x120:rate=10:duration=20",
                    "-f", "lavfi", "-i", "sine=frequency=500:sample_rate=16160:duration=20",
                    "-c:v", "mjpeg", "-pix_fmt", "yuvj422p", "-c:a", "adpcm_ima_wav", source)
        result = self.cli("-i", source, "--format", "mp4", "--audio", "keep",
                          "--cut-no-signal", "off", "--deinterlace", "off", "--denoise", "off")
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
        report = json.loads((self.root / "unusual-rate_converted.mp4.report.json").read_text())
        self.assertEqual(report["validation"]["decoded_frames"], 200)
        self.assertEqual(report["plan"]["selected"]["audio_sample_rate"], 16000)
        audio = report["validation"]["audio"]
        self.assertEqual(audio["target_samples"], 320000)
        self.assertLessEqual(abs(audio["decoded_samples"] - audio["target_samples"]), 2048)


if __name__ == "__main__":
    unittest.main()
