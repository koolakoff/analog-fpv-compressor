"""Render the real GUI widgets for the README without starting a compression job."""

from pathlib import Path

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from analog_fpv_compressor.gui.window import MainWindow

ROOT = Path(__file__).resolve().parents[1]


def main():
    app = QApplication([])
    evidence = ROOT / "outputs/readme-preview"
    evidence.mkdir(parents=True, exist_ok=True)
    preferences = QSettings(str(evidence / "preferences.ini"), QSettings.Format.IniFormat)
    preferences.setValue("language", "ru")
    window = MainWindow(preferences, log_path=evidence / "fpv-compress.log")
    window.add_paths(sorted((ROOT / "examples").glob("*.AVI")))
    window.resize(1000, 760)
    window.ensurePolished()
    app.processEvents()
    for language in ("en", "ru", "uk", "sk"):
        window.language.setCurrentIndex(window.language.findData(language))
        app.processEvents()
        destination = ROOT / "docs/images" / f"gui-{language}.png"
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not window.grab().save(str(destination)):
            raise RuntimeError("Cannot save GUI screenshot")
    window.close()


if __name__ == "__main__":
    main()
