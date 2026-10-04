"""Verify CLI contracts independently of installed FFmpeg and local videos."""

import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

import analog_fpv_compressor
from analog_fpv_compressor.cli import EventLogger, create_parser, format_timestamp, main, settings_from_args
from analog_fpv_compressor.models import Analysis, CancelToken, Event, Plan, Settings, to_dict


class CliTests(unittest.TestCase):
    def test_named_hevc_preset_reaches_core_unchanged(self):
        options = create_parser().parse_args(["-i", "input.AVI", "-o", "output.mkv",
                                              "--codec", "hevc", "--preset", "medium"])
        settings = settings_from_args(options)
        self.assertEqual(settings.codec, "hevc")
        self.assertEqual(settings.preset, "medium")

    def test_requested_manual_options_and_defaults_are_preserved(self):
        options = create_parser().parse_args(["-i", "input α.AVI", "-o", "output.mkv", "--audio", "keep",
                                               "--crf", "42", "--denoise", "off", "--cut-no-signal", "off"])
        settings = settings_from_args(options)
        self.assertEqual(settings.audio, "keep")
        self.assertEqual(settings.crf, 42)
        self.assertEqual(settings.denoise, "off")
        self.assertEqual(settings.deinterlace, "auto")
        self.assertEqual(settings.report_path, Path("output.mkv.report.json"))
        self.assertEqual(to_dict(settings)["input_path"], "input α.AVI")

    def test_machine_events_are_json_lines_and_human_output_uses_stderr(self):
        with tempfile.TemporaryDirectory() as directory:
            stdout, stderr = io.StringIO(), io.StringIO()
            log = Path(directory) / "job.log"
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                with EventLogger(log, True) as emit:
                    emit(Event("plan.resolved", {"removed_intervals": ((76.25, 143.75),)}))
            event = json.loads(stdout.getvalue())
            self.assertEqual(event["schema_version"], 1)
            self.assertEqual(event["data"]["removed_intervals"], [[76.25, 143.75]])
            self.assertIn("[00:01:16.250, 00:02:23.750)", stderr.getvalue())
            self.assertEqual(log.read_text(encoding="utf-8"), stderr.getvalue())

    def test_analyze_only_writes_plan_and_never_calls_encoder(self):
        with tempfile.TemporaryDirectory() as directory:
            input_path, output = Path(directory) / "input.AVI", Path(directory) / "output.mkv"
            input_path.write_bytes(b"local fixture")
            analysis = Analysis(input_path, {"streams": []})
            def build(settings, measured):
                return Plan(settings, measured, {"audio": "remove"}, ((0., 1.),), (), (), {}, "test")
            def unexpected(*args, **kwargs):
                self.fail("Analyze-only called the encoder")
            controller = SimpleNamespace(validate_settings=lambda settings: None,
                                         analyze=lambda *args, **kwargs: analysis, build_plan=build)
            processing = SimpleNamespace(execute=unexpected)
            with patch.object(analog_fpv_compressor, "controller", controller, create=True), \
                 patch.object(analog_fpv_compressor, "processing", processing, create=True), \
                 contextlib.redirect_stderr(io.StringIO()):
                code = main(["-i", str(input_path), "-o", str(output), "--analyze-only"])
            self.assertEqual(code, 0)
            self.assertFalse(output.exists())
            report = json.loads(Path(str(output) + ".report.json").read_text(encoding="utf-8"))
            self.assertEqual(report["status"], "analyzed")
            self.assertEqual(report["plan"]["selected"]["audio"], "remove")

    def test_existing_log_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "existing.log"
            path.write_text("keep this", encoding="utf-8")
            with self.assertRaises(FileExistsError):
                with EventLogger(path):
                    self.fail("Existing log opened")
            self.assertEqual(path.read_text(encoding="utf-8"), "keep this")

    def test_analysis_cancellation_exception_has_exit_code_130(self):
        from analog_fpv_compressor.runtime import CancelledError
        with tempfile.TemporaryDirectory() as directory:
            input_path, output = Path(directory) / "input.AVI", Path(directory) / "output.mkv"
            input_path.write_bytes(b"local fixture")
            def cancelled(*args, **kwargs):
                raise CancelledError("Processing was cancelled")
            controller = SimpleNamespace(validate_settings=lambda settings: None, analyze=cancelled)
            with patch.object(analog_fpv_compressor, "controller", controller, create=True), \
                 contextlib.redirect_stderr(io.StringIO()):
                code = main(["-i", str(input_path), "-o", str(output)])
            self.assertEqual(code, 130)
            self.assertFalse(output.exists())
            events = [json.loads(line) for line in Path(str(output) + ".log.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertEqual(events[-1]["code"], "job.cancelled")

    def test_existing_event_log_does_not_get_removed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "new.log"
            json_path = Path(str(path) + ".jsonl")
            json_path.write_text("keep this", encoding="utf-8")
            with self.assertRaises(FileExistsError):
                with EventLogger(path):
                    self.fail("Existing event log opened")
            self.assertFalse(path.exists())
            self.assertEqual(json_path.read_text(encoding="utf-8"), "keep this")


if __name__ == "__main__":
    unittest.main()
