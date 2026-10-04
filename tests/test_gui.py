"""Exercise translated widgets and the actual worker/core path without a visible window."""

import json
import os
from pathlib import Path
import string
import subprocess
import tempfile
import unittest
from unittest.mock import patch

try:
    from PySide6.QtCore import QEventLoop, QSettings, QTimer
    from PySide6.QtWidgets import QApplication
    from analog_fpv_compressor.gui.i18n import LANGUAGES, catalog, tr
    from analog_fpv_compressor.gui.window import MainWindow, STAGES
except ImportError:
    QApplication = None

from analog_fpv_compressor.runtime import discover_tools


@unittest.skipIf(QApplication is None, "Install the gui extra to run desktop checks")
class GuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if os.name != "nt" and not os.environ.get("DISPLAY"):
            os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        preferences = QSettings(str(self.root / "preferences.ini"), QSettings.Format.IniFormat)
        preferences.setValue("language", "en")
        self.window = MainWindow(preferences, log_path=self.root / "fpv-compress.log")

    def tearDown(self):
        self.window.stop()
        self.wait_finished()
        self.window.close()
        self.app.processEvents()
        self.temporary.cleanup()

    def wait_finished(self):
        if self.window.thread is not None:
            loop = QEventLoop()
            timer = QTimer()
            timer.setSingleShot(True)
            timer.timeout.connect(loop.quit)
            self.window.thread.finished.connect(loop.quit)
            timer.start(60000)
            loop.exec()
            timer.stop()
        self.assertIsNone(self.window.thread, "Worker did not finish or cancel within 60 seconds")

    def video(self, name="source.mkv", snow=False):
        try:
            tools = discover_tools(os.environ.get("FPV_FFMPEG_BIN"))
        except ValueError as error:
            self.skipTest(str(error))
        source = self.root / name
        graph = "testsrc2=size=160x120:rate=10:duration=2"
        if snow:
            scene = "testsrc2=size=160x120:rate=10:duration=1"
            noise = "color=c=gray:size=160x120:rate=10:duration=2,noise=alls=100:allf=t+u"
            graph = f"{scene}[a];{noise}[b];{scene}[c];[a][b][c]concat=n=3:v=1:a=0"
        subprocess.run([tools["ffmpeg"], "-v", "error", "-f", "lavfi", "-i", graph,
                        "-c:v", "ffv1", str(source)], check=True, capture_output=True)
        self.window.ffmpeg_dir.setText(str(Path(tools["ffmpeg"]).parent))
        self.window.preset.setText("10")
        self.window.threads.setValue(2)
        self.window.snow.setCurrentIndex(self.window.snow.findData("remove"))
        self.window.deinterlace.setCurrentIndex(self.window.deinterlace.findData("off"))
        self.window.output_location.setCurrentIndex(self.window.output_location.findData("folder"))
        self.window.output_dir.setText(str(self.root / "converted"))
        return source

    def test_catalogs_cover_widgets_and_preserve_template_fields(self):
        english = catalog("en")
        sources = {source for _, source, _ in self.window.bindings}
        sources.update(source for _, choices in self.window.combos for _, source in choices)
        sources.update(STAGES.values())
        self.assertFalse(sources - english.keys())
        def placeholders(text):
            return {field for _, field, _, _ in string.Formatter().parse(text) if field is not None}
        for language in LANGUAGES:
            messages = catalog(language)
            self.assertEqual(messages.keys(), english.keys())
            for source, translated in messages.items():
                self.assertEqual(placeholders(source), placeholders(translated), (language, source))

    def test_language_switch_keeps_settings_and_persists_selection(self):
        self.window.denoise.setCurrentIndex(self.window.denoise.findData("strong"))
        requested = self.window.options()
        for language in LANGUAGES:
            self.window.language.setCurrentIndex(self.window.language.findData(language))
            self.assertEqual(self.window.add_button.text(), catalog(language)["Add files…"])
            self.assertEqual(self.window.options(), requested)
            self.assertEqual(self.window.preferences.value("language"), language)
        self.assertEqual(tr("Untranslated future message"), "Untranslated future message")

    def test_output_modes_and_input_deduplication(self):
        source = self.root / "input.avi"
        source.touch()
        self.window.add_paths([source, source])
        self.assertEqual(self.window.queue.topLevelItemCount(), 1)
        self.assertEqual(self.window.options()["output_suffix"], "_converted")
        self.assertEqual(self.window.options()["audio"], "remove")
        self.assertEqual(self.window.options()["crf"], "auto")
        self.window.snow.setCurrentIndex(self.window.snow.findData("split"))
        self.assertTrue(self.window.options()["split_flights"])
        self.assertEqual(self.window.options()["cut_no_signal"], "auto")
        self.window.snow.setCurrentIndex(self.window.snow.findData("keep"))
        self.assertFalse(self.window.options()["split_flights"])
        self.assertEqual(self.window.options()["cut_no_signal"], "off")

    def test_real_worker_continues_after_bad_input_and_remains_responsive(self):
        source = self.video()
        bad = self.root / "bad.avi"
        bad.write_bytes(b"invalid DVR")
        self.window.add_paths([bad, source])
        ticks = []
        timer = QTimer()
        timer.timeout.connect(lambda: ticks.append(1))
        timer.start(5)
        self.window.start()
        self.assertFalse(self.window.settings_panel.isEnabled())
        self.assertTrue(self.window.language.isEnabled())
        QTimer.singleShot(20, lambda: self.window.language.setCurrentIndex(self.window.language.findData("sk")))
        self.wait_finished()
        timer.stop()
        self.assertGreater(len(ticks), 2)
        self.assertEqual(self.window.summary["succeeded"], 1)
        self.assertEqual(self.window.summary["failed"], 1)
        self.assertEqual(self.window.records[str(bad)]["state"], "Failed")
        good = self.window.records[str(source)]
        self.assertEqual(good["state"], "Ready")
        report = self.log_data("processing_completed")["report"]
        self.assertTrue(report["validation"]["full_decode_passed"])
        self.assertEqual(report["validation"]["decoded_frames"], 20)
        self.assertEqual(self.window.language.currentData(), "sk")
        self.assertTrue(self.window.settings_panel.isEnabled())
        records = [json.loads(line) for line in self.window.session_log.path.read_text(encoding="utf-8").splitlines()]
        failure = next(record for record in records if record["code"] == "job.failed")
        self.assertIn("traceback", failure["data"])
        self.assertIn("invalid", failure["data"]["message"].lower())
        self.assertFalse(any(record["code"] == "progress" and record["data"]["state"] == "running" for record in records))
        self.assertFalse(list((self.root / "converted").glob("*.log*")))
        with patch("analog_fpv_compressor.gui.window.QDesktopServices.openUrl", return_value=True) as opening:
            self.window.open_log_button.click()
            self.assertEqual(Path(opening.call_args.args[0].toLocalFile()), self.window.session_log.path)

    def test_multiple_queues_share_log_until_window_restart(self):
        source = self.video()
        self.window.add_paths([source])
        self.window.analyze_only.setChecked(True)
        for suffix in ("_first", "_second"):
            self.window.suffix.setText(suffix)
            self.window.start()
            self.wait_finished()
        path = self.window.session_log.path
        records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(sum(record["code"] == "session.started" for record in records), 1)
        self.assertEqual(sum(record["code"] == "job.analyzed" for record in records), 2)
        self.window.close()
        self.window = MainWindow(self.window.preferences, log_path=path)
        records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        self.assertEqual([record["code"] for record in records], ["session.started"])

    def test_real_split_creates_selectable_result_children_and_reports(self):
        source = self.video(snow=True)
        self.window.add_paths([source])
        self.window.snow.setCurrentIndex(self.window.snow.findData("split"))
        self.window.start()
        self.wait_finished()
        record = self.window.records[str(source)]
        self.assertEqual(record["state"], "Ready")
        self.assertEqual(record["item"].childCount(), 2)
        for index in range(2):
            self.window.queue.setCurrentItem(record["item"].child(index))
            paths = self.window.selection_paths()
            self.assertTrue(Path(paths["output"]).is_file())
            self.assertTrue(self.window.open_result.isEnabled())
            with patch("analog_fpv_compressor.gui.window.QDesktopServices.openUrl", return_value=True) as opening:
                self.window.open_selected("output")
                self.window.open_selected("folder")
                self.assertEqual([Path(call.args[0].toLocalFile()) for call in opening.call_args_list],
                                 [Path(paths["output"]), Path(paths["output"]).parent])
        self.assertEqual(self.log_data("flights.summary")["completed_outputs"], 2)
        self.assertFalse(list((self.root / "converted").glob("*.json")))

    def test_cancel_at_encode_does_not_publish_or_start_next_source(self):
        source = self.video()
        second = self.root / "second.mkv"
        second.write_bytes(source.read_bytes())
        self.window.add_paths([source, second])
        self.window.start()
        def stop_at_encode(event):
            if event.code == "progress" and event.data["stage"] == "encode":
                self.window.stop()
        self.window.worker.event.connect(stop_at_encode)
        self.wait_finished()
        self.assertTrue(self.window.summary["cancelled"])
        self.assertFalse((self.root / "converted/source_converted.mkv").exists())
        self.assertFalse((self.root / "converted/second_converted.mkv").exists())
        self.assertEqual(self.window.records[str(second)]["state"], "Stopped")

    def test_close_while_running_requests_cancellation_and_waits_for_cleanup(self):
        source = self.video()
        self.window.add_paths([source])
        self.window.start()
        with patch("analog_fpv_compressor.gui.window.QMessageBox.exec", return_value=16384):
            self.window.close()
        self.assertTrue(self.window.pending_close)
        self.assertTrue(self.window.cancel_token.is_cancelled())
        self.wait_finished()
        self.assertFalse((self.root / "converted/source_converted.mkv").exists())
        self.assertFalse(list((self.root / "converted").glob("*.partial*")))

    def test_split_failure_keeps_ready_child_and_opens_partial_master_report(self):
        from analog_fpv_compressor import processing
        source = self.video(snow=True)
        self.window.add_paths([source])
        self.window.snow.setCurrentIndex(self.window.snow.findData("split"))
        original = processing.execute
        calls = []
        def fail_second(plan, **options):
            calls.append(plan)
            if len(calls) == 2:
                raise processing.ProcessingError("Expected second-part failure")
            return original(plan, **options)
        with patch.object(processing, "execute", side_effect=fail_second):
            self.window.start()
            self.wait_finished()
        record = self.window.records[str(source)]
        self.assertEqual(record["state"], "Failed")
        self.assertEqual(record["item"].childCount(), 1)
        self.window.queue.setCurrentItem(record["item"])
        summary = self.log_data("flights.summary")
        self.assertEqual(summary["status"], "failed")
        self.assertEqual(summary["completed_outputs"], 1)
        self.window.queue.setCurrentItem(record["item"].child(0))
        self.assertTrue(self.window.open_result.isEnabled())

    def log_data(self, code):
        events = [json.loads(line) for line in self.window.session_log.path.read_text(encoding="utf-8").splitlines()]
        return next(event["data"] for event in events if event["code"] == code)

    def test_default_split_and_system_language_fallback_and_icons(self):
        self.assertEqual(self.window.snow.currentData(), "split")
        self.assertFalse(self.window.windowIcon().isNull())
        self.assertFalse(self.app.windowIcon().isNull())
        from analog_fpv_compressor.gui.i18n import system_language
        from PySide6.QtCore import QLocale
        for locale, expected in (("uk_UA", "uk"), ("ru_RU", "ru"), ("sk_SK", "sk"), ("en_US", "en"), ("fr_FR", "en")):
            with patch("analog_fpv_compressor.gui.i18n.QLocale.system", return_value=QLocale(locale)):
                self.assertEqual(system_language(), expected)


if __name__ == "__main__":
    unittest.main()
