"""Explicit frozen-bundle verification used by the release tooling only."""

import json
from pathlib import Path

from PySide6.QtCore import QSettings, QTimer, qVersion
from PySide6.QtWidgets import QApplication

from .window import MainWindow


def verify(configuration):
    config = json.loads(Path(configuration).read_text(encoding="utf-8"))
    evidence = Path(config["evidence"])
    evidence.mkdir(parents=True, exist_ok=True)
    app = QApplication([])
    preferences = QSettings(str(evidence / "settings.ini"), QSettings.Format.IniFormat)
    preferences.setValue("language", "en")
    window = MainWindow(preferences)
    window.add_paths([config["input"]])
    window.output_location.setCurrentIndex(window.output_location.findData("folder"))
    window.output_dir.setText(config["output"])
    window.ffmpeg_dir.setText(config["ffmpeg_dir"])
    checks = {"qt_version": qVersion(), "window_icon": not window.windowIcon().isNull(),
              "application_icon": not app.windowIcon().isNull(), "default_snow": window.snow.currentData(),
              "translations": {}}
    for language in ("en", "ru", "uk", "sk"):
        window.language.setCurrentIndex(window.language.findData(language))
        app.processEvents()
        checks["translations"][language] = window.add_button.text()
        window.grab().save(str(evidence / (language + ".png")))
    window.language.setCurrentIndex(window.language.findData("en"))
    timer = QTimer()
    timer.setSingleShot(True)
    timer.timeout.connect(window.stop)
    def start():
        window.start()
        if window.thread is None:
            app.quit()
        else:
            window.thread.finished.connect(app.quit)
    QTimer.singleShot(0, start)
    timer.start(120000)
    app.exec()
    timer.stop()
    checks["summary"] = window.summary
    window.close()
    records = [json.loads(line) for line in window.session_log.path.read_text(encoding="utf-8").splitlines()]
    checks["parts"] = [record["data"]["report"] for record in records if record["code"] == "processing_completed"]
    checks["passed"] = bool(window.summary and not window.summary["failed"] and not window.summary["cancelled"]
                            and checks["window_icon"] and checks["application_icon"]
                            and checks["default_snow"] == "split" and len(set(checks["translations"].values())) == 4)
    (evidence / "gui-verification.json").write_text(json.dumps(checks, indent=2, ensure_ascii=False), encoding="utf-8")
    return 0 if checks["passed"] else 1
