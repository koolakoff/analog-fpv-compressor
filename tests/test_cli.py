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
    def test_input_list_repeated_flags_and_optional_output(self):
        args = create_parser().parse_args(["-i", "a.avi", "b.avi", "-i", "*.AVI", "--split-flights"])
        self.assertEqual(args.input, ["a.avi", "b.avi", "*.AVI"])
        self.assertIsNone(args.output)
        self.assertTrue(args.split_flights)

    def test_default_output_name_can_be_resolved_without_filesystem_access(self):
        args = create_parser().parse_args(["-i", "somewhere/input.avi", "--format", "mp4"])
        self.assertEqual(settings_from_args(args).output_path, Path("somewhere/input_converted.mp4"))

    def test_batch_continues_after_one_analysis_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            sources = [Path(directory) / name for name in ("bad.avi", "good.avi")]
            for source in sources:
                source.write_bytes(b"fixture")
            calls = []
            def analyze(settings, **options):
                calls.append(settings.input_path)
                if settings.input_path == sources[0]:
                    raise ValueError("Invalid video fixture")
                return Analysis(settings.input_path, {"streams": []})
            def build(settings, analysis):
                return Plan(settings, analysis, {}, ((0., 1.),), (), (), {}, "test")
            def execute(plan, **options):
                return SimpleNamespace(output_path=plan.settings.output_path, report_path=plan.settings.report_path,
                                       bytes=123, duration_seconds=1.)
            controller = SimpleNamespace(validate_settings=lambda settings: None, analyze=analyze, build_plan=build)
            stdout = io.StringIO()
            with patch.object(analog_fpv_compressor, "controller", controller, create=True), \
                 patch.object(analog_fpv_compressor, "processing", SimpleNamespace(execute=execute), create=True), \
                 contextlib.redirect_stdout(stdout), \
                 contextlib.redirect_stderr(io.StringIO()):
                code = main(["-i", *map(str, sources), "--events-jsonl"])
            self.assertEqual(code, 1)
            self.assertEqual(calls, sources)
            events = [json.loads(line) for line in (Path(directory) / "good_converted.mkv.log.jsonl").read_text().splitlines()]
            self.assertEqual(events[-1]["code"], "job.completed")
            self.assertEqual(events[-1]["data"]["job_index"], 2)
            completed = json.loads(stdout.getvalue().splitlines()[-1])
            self.assertEqual(completed["code"], "batch.completed")
            self.assertEqual(completed["data"], {"inputs": 2, "succeeded": 1, "failed": 1})
            self.assertIn("timestamp", completed)

    def test_split_failure_keeps_completed_parts_and_cancellation_stops_batch(self):
        from analog_fpv_compressor.processing import ProcessingError
        from analog_fpv_compressor.runtime import CancelledError
        for error_type, expected_code in ((ProcessingError, 1), (CancelledError, 130)):
            with self.subTest(error_type=error_type), tempfile.TemporaryDirectory() as directory:
                sources = [Path(directory) / name for name in ("first.avi", "next.avi")]
                for source in sources:
                    source.write_bytes(b"fixture")
                calls = []
                def analyze(settings, **options):
                    return Analysis(settings.input_path, {"streams": [{"codec_type": "video", "time_base": "1/1"}]},
                                    frame_pts=(0, 3, 6))
                def build(settings, analysis):
                    return Plan(settings, analysis, {}, ((0., 1.), (3., 4.), (6., 7.)),
                                ((1., 3.), (4., 6.)), (), {}, "test")
                def execute(plan, **options):
                    calls.append(plan.settings.input_path)
                    if len(calls) == 2:
                        raise error_type("Stopped during second part")
                    plan.settings.output_path.write_bytes(b"verified fixture")
                    plan.settings.report_path.write_text('{}', encoding="utf-8")
                    return SimpleNamespace(output_path=plan.settings.output_path, report_path=plan.settings.report_path,
                                           bytes=16, duration_seconds=1.)
                controller = SimpleNamespace(validate_settings=lambda settings: None, analyze=analyze, build_plan=build)
                with patch.object(analog_fpv_compressor, "controller", controller, create=True), \
                     patch.object(analog_fpv_compressor, "processing", SimpleNamespace(execute=execute), create=True), \
                     contextlib.redirect_stderr(io.StringIO()):
                    code = main(["-i", *map(str, sources), "--split-flights"])
                self.assertEqual(code, expected_code)
                self.assertEqual((Path(directory) / "first_converted_1.mkv").read_bytes(), b"verified fixture")
                summary = json.loads((Path(directory) / "first_converted.mkv.report.json").read_text())
                self.assertEqual(summary["completed_outputs"], 1)
                self.assertEqual(summary["status"], "cancelled" if expected_code == 130 else "failed")
                self.assertFalse((Path(directory) / "first_converted_3.mkv").exists())
                self.assertEqual(len(calls), 2 if expected_code == 130 else 5)

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
