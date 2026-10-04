"""Check non-square source pixels survive explicit scaling in both containers."""

from dataclasses import replace
from fractions import Fraction
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from analog_fpv_compressor import controller, processing
from analog_fpv_compressor.models import Settings


class AspectIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        directory = os.environ.get("FPV_FFMPEG_BIN")
        cls.ffmpeg = str(Path(directory) / "ffmpeg.exe") if directory else shutil.which("ffmpeg")
        cls.ffprobe = str(Path(directory) / "ffprobe.exe") if directory else shutil.which("ffprobe")
        if not cls.ffmpeg or not cls.ffprobe:
            raise unittest.SkipTest("Set FPV_FFMPEG_BIN or install FFmpeg to run aspect integration checks")
        cls.ffmpeg_dir = Path(cls.ffmpeg).parent

    def test_non_square_pixels_keep_display_aspect_after_arbitrary_scaling(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for source_sar in ("8/9", "16/15"):
                source = root / f"source-{source_sar.replace('/', '-')}.mkv"
                subprocess.run([self.ffmpeg, "-v", "error", "-f", "lavfi", "-i",
                                "testsrc2=size=720x480:rate=10:duration=2", "-vf", f"setsar={source_sar}",
                                "-c:v", "ffv1", "-color_range", "tv", str(source)], check=True)
                settings = Settings(source, root / "unused.mkv", cut_no_signal="off", deinterlace="off",
                                    denoise="off", scale="200x100", preset=8, threads=2, ffmpeg_dir=self.ffmpeg_dir)
                analysis = controller.analyze(settings)
                source_video = next(s for s in analysis.probe["streams"] if s["codec_type"] == "video")
                source_dar = Fraction(source_video["display_aspect_ratio"].replace(":", "/"))
                for suffix in (".mkv", ".mp4"):
                    with self.subTest(source_sar=source_sar, container=suffix):
                        output = root / f"scaled-{source_sar.replace('/', '-')}{suffix}"
                        chosen = replace(settings, output_path=output)
                        result = processing.execute(controller.build_plan(chosen, analysis))
                        data = json.loads(subprocess.check_output([self.ffprobe, "-v", "error", "-show_streams",
                                                                  "-of", "json", str(result.output_path)]))
                        video = next(s for s in data["streams"] if s["codec_type"] == "video")
                        self.assertEqual((video["width"], video["height"]), (200, 100))
                        self.assertEqual(result.report["validation"]["decoded_frames"], len(analysis.timestamps))
                        output_dar = Fraction(video["display_aspect_ratio"].replace(":", "/"))
                        print(f"Source SAR {source_sar}, {suffix}: source DAR {source_dar}, output DAR {output_dar}")
                        if suffix == ".mp4":
                            self.assertEqual(output_dar, source_dar)
                        else:
                            # Matroska display dimensions are integer pixels;
                            # one display-width pixel changes DAR by 1/height.
                            self.assertLessEqual(abs(output_dar - source_dar), Fraction(1, video["height"]))

    def test_unspecified_source_aspect_preserves_raster_geometry(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "unknown aspect.AVI"
            subprocess.run([self.ffmpeg, "-v", "error", "-f", "lavfi", "-i",
                            "testsrc2=size=720x480:rate=10:duration=1", "-vf", "setsar=0",
                            "-c:v", "mjpeg", "-pix_fmt", "yuvj422p", "-color_range", "pc", str(source)], check=True)
            settings = Settings(source, root / "scaled.mkv", cut_no_signal="off", deinterlace="off",
                                denoise="off", scale="480x360", preset=8, threads=2, ffmpeg_dir=self.ffmpeg_dir)
            analysis = controller.analyze(settings)
            source_video = next(s for s in analysis.probe["streams"] if s["codec_type"] == "video")
            self.assertIn(source_video.get("sample_aspect_ratio"), (None, "0:1"))
            result = processing.execute(controller.build_plan(settings, analysis))
            data = json.loads(subprocess.check_output([self.ffprobe, "-v", "error", "-show_streams",
                                                      "-of", "json", str(result.output_path)]))
            video = next(s for s in data["streams"] if s["codec_type"] == "video")
            self.assertEqual((video["width"], video["height"]), (480, 360))
            self.assertEqual(Fraction(video["display_aspect_ratio"].replace(":", "/")), Fraction(3, 2))
            self.assertEqual(result.report["validation"]["decoded_frames"], len(analysis.timestamps))


if __name__ == "__main__":
    unittest.main()
