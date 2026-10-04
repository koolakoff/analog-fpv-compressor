"""Load external translation catalogs through Qt's translation mechanism."""

from importlib.resources import files
import json

from PySide6.QtCore import QCoreApplication, QLocale, QTranslator

LANGUAGES = {"en": "English", "ru": "Русский", "uk": "Українська", "sk": "Slovenčina"}


def catalog(language):
    return json.loads(files(__package__).joinpath("translations", language + ".json").read_text(encoding="utf-8"))


class CatalogTranslator(QTranslator):
    """Use readable JSON catalogs with English Qt source strings as fallback."""

    def __init__(self, language, parent=None):
        super().__init__(parent)
        self.messages = catalog(language)

    def isEmpty(self):
        return not self.messages

    def translate(self, context, sourceText, disambiguation=None, n=-1):
        if context == "FPV":
            return self.messages.get(sourceText, sourceText)
        return sourceText


def tr(source, **values):
    return QCoreApplication.translate("FPV", source).format(**values)


def system_language():
    code = QLocale.system().name().split("_")[0]
    return code if code in LANGUAGES else "en"
