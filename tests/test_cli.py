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
from analog_fpv_compressor.cli import EventLogger, create_parser, format_timestamp, main as cli_main, settings_from_args
from analog_fpv_compressor.models import Analysis, CancelToken, Event, Plan, Settings, to_dict


def main(argv):
    source = Path(argv[argv.index("-i") + 1])
    if "--log-file" not in argv:
        argv = [*argv, "--log-file", str(source.parent / "session.log")]
    if "--split-flights" not in argv:
        argv = [*argv, "--no-split-flights"]
    return cli_main(argv)


def log_data(directory, code):
    events = [json.loads(line) for line in (Path(directory) / "session.log").read_text(encoding="utf-8").splitlines()]
    return next(event["data"] for event in events if event["code"] == code)


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
                code = main(["-i", *map(str, sources), "--events-jsonl", "--log-file", str(Path(directory) / "session.log")])
            self.assertEqual(code, 1)
            self.assertEqual(calls, sources)
            events = [json.loads(line) for line in (Path(directory) / "session.log").read_text().splitlines()]
            job = next(event for event in events if event["code"] == "job.completed")
            self.assertEqual(job["data"]["job_index"], 2)
            self.assertTrue(events[-1]["data"]["failed"])
            completed = next(json.loads(line) for line in stdout.getvalue().splitlines() if json.loads(line)["code"] == "batch.completed")
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
                    return SimpleNamespace(output_path=plan.settings.output_path, report_path=plan.settings.report_path,
                                           bytes=16, duration_seconds=1.)
                controller = SimpleNamespace(validate_settings=lambda settings: None, analyze=analyze, build_plan=build)
                with patch.object(analog_fpv_compressor, "controller", controller, create=True), \
                     patch.object(analog_fpv_compressor, "processing", SimpleNamespace(execute=execute), create=True), \
                     contextlib.redirect_stderr(io.StringIO()):
                    code = main(["-i", *map(str, sources), "--split-flights"])
                self.assertEqual(code, expected_code)
                self.assertEqual((Path(directory) / "first_converted_1.mkv").read_bytes(), b"verified fixture")
                summary = log_data(directory, "flights.summary")
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
        self.assertIsNone(settings.report_path)
        self.assertEqual(to_dict(settings)["input_path"], "input α.AVI")

    def test_machine_events_are_json_lines_and_human_output_uses_stderr(self):
        with tempfile.TemporaryDirectory() as directory:
            stdout, stderr = io.StringIO(), io.StringIO()
            log = Path(directory) / "job.log"
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                with EventLogger(log, True) as emit:
                    emit(Event("plan.resolved", {"removed_intervals": ((76.25, 143.75),)}))
            event = next(json.loads(line) for line in stdout.getvalue().splitlines() if json.loads(line)["code"] == "plan.resolved")
            self.assertEqual(event["schema_version"], 1)
            self.assertEqual(event["data"]["removed_intervals"], [[76.25, 143.75]])
            self.assertIn("[00:01:16.250, 00:02:23.750)", stderr.getvalue())
            self.assertEqual([json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()],
                             [json.loads(line) for line in stdout.getvalue().splitlines()])
            self.assertFalse(Path(str(log) + ".jsonl").exists())

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
            report = log_data(directory, "job.analyzed")["report"]
            self.assertFalse(list(Path(directory).glob("*.json")))
            self.assertEqual(report["status"], "analyzed")
            self.assertEqual(report["plan"]["selected"]["audio"], "remove")

    def test_existing_log_is_overwritten_on_launch(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "existing.log"
            path.write_text("keep this", encoding="utf-8")
            with EventLogger(path, echo=False):
                pass
            self.assertNotIn("keep this", path.read_text(encoding="utf-8"))
            self.assertEqual(len(path.read_text(encoding="utf-8").splitlines()), 2)

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
                code = main(["-i", str(input_path), "-o", str(output), "--log-file", str(Path(directory) / "session.log")])
            self.assertEqual(code, 130)
            self.assertFalse(output.exists())
            events = [json.loads(line) for line in (Path(directory) / "session.log").read_text(encoding="utf-8").splitlines()]
            self.assertEqual(events[-2]["code"], "job.cancelled")

    def test_existing_event_log_does_not_get_removed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "new.log"
            json_path = Path(str(path) + ".jsonl")
            json_path.write_text("keep this", encoding="utf-8")
            with EventLogger(path, echo=False):
                pass
            self.assertTrue(path.exists())
            self.assertEqual(json_path.read_text(encoding="utf-8"), "keep this")


if __name__ == "__main__":
    unittest.main()
