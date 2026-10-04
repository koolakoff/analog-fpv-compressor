"""Check session lifetime, progress suppression and recoverable native errors."""

import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from analog_fpv_compressor.cli import main
from analog_fpv_compressor.models import Event
from analog_fpv_compressor.processing import _run, ProcessingError
from analog_fpv_compressor.runner import EventLogger, default_log_path


class LoggingTests(unittest.TestCase):
    def records(self, path):
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

    def test_progress_is_live_but_not_saved_per_frame(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "session.log"
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout), EventLogger(path, machine_stdout=True, echo=False) as log:
                for state, count in [("started", 0), *[("running", i) for i in range(100)], ("completed", 100)]:
                    log(Event("progress", {"state": state, "completed": count, "stage": "encode"}))
                log(Event("analysis_progress", {"frames": 100}))
            saved = self.records(path)
            self.assertEqual([event["code"] for event in saved], ["session.started", "progress", "progress", "session.finished"])
            self.assertEqual(len(stdout.getvalue().splitlines()), 105)
            self.assertIn("application_version", saved[0]["data"])
            self.assertFalse(Path(str(path) + ".jsonl").exists())

    def test_input_and_report_collision_never_truncates_file(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "input.avi"
            source.write_bytes(b"keep source")
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(main(["-i", str(source), "--log-file", str(source)]), 1)
            self.assertEqual(source.read_bytes(), b"keep source")
            report = Path(directory) / "input_converted.mkv"
            report.write_text("keep report", encoding="utf-8")
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(main(["-i", str(source), "--log-file", str(report)]), 1)
            self.assertEqual(report.read_text(encoding="utf-8"), "keep report")
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(main(["-i", str(Path(directory) / "missing.avi"), str(Path(directory) / "*.avi"),
                                       "--log-file", str(source)]), 1)
            self.assertEqual(source.read_bytes(), b"keep source")

    def test_missing_input_is_recorded_and_next_launch_replaces_session(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "session.log"
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(main(["-i", str(Path(directory) / "missing.avi"), "--log-file", str(path)]), 1)
            records = self.records(path)
            self.assertEqual(records[-2]["code"], "application.failed")
            self.assertIn("traceback", records[-2]["data"])
            self.assertTrue(records[-1]["data"]["failed"])
            with EventLogger(path, echo=False):
                pass
            self.assertEqual(len(self.records(path)), 2)
            self.assertFalse(self.records(path)[-1]["data"]["failed"])

    def test_native_failure_keeps_diagnostic_tail_in_session(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "session.log"
            with EventLogger(path, echo=False) as log, tempfile.TemporaryFile(mode="w+", encoding="utf-8") as diagnostics:
                try:
                    _run([sys.executable, "-c", "import sys; sys.stderr.write('native diagnostic marker\\n'); sys.exit(3)"], diagnostics)
                except ProcessingError as error:
                    log(Event("job.failed", {"message": str(error)}))
                else:
                    self.fail("Native failure was not reported")
            failure = self.records(path)[1]
            self.assertIn("native diagnostic marker", failure["data"]["message"])
            self.assertIn("status 3", failure["data"]["message"])
            self.assertEqual(list(Path(directory).iterdir()), [path])

    def test_default_location_uses_launcher_and_frozen_executable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(sys, "argv", [str(root / "fpv-compress-gui.exe")]), patch.object(sys, "frozen", False, create=True):
                self.assertEqual(default_log_path(), root / "fpv-compress.log")
            with patch.object(sys, "argv", ["arbitrary"]), patch.object(sys, "executable", str(root / "app.exe")), patch.object(sys, "frozen", True, create=True):
                self.assertEqual(default_log_path(), root / "fpv-compress.log")
