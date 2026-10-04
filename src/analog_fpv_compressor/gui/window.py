"""Classic desktop queue with immutable job settings and live language switching."""

from collections import deque
import json
from pathlib import Path
import sys

from PySide6.QtCore import QSettings, QThread, Qt, QUrl, Slot
from PySide6.QtGui import QDesktopServices, QIcon, QKeySequence, QShortcut
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox, QComboBox,
                              QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QHeaderView, QLabel,
                              QLineEdit, QMainWindow, QMessageBox, QPlainTextEdit,
                              QProgressBar, QPushButton, QScrollArea, QSpinBox,
                              QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from .. import __version__
from ..models import CancelToken
from ..runner import EventLogger
from .i18n import CatalogTranslator, LANGUAGES, system_language, tr
from .worker import BatchWorker

STAGES = {"prepare": "Checking tools", "probe": "Reading video", "snow": "Finding white noise",
          "interlace": "Checking interlacing", "encode": "Encoding", "validate": "Checking result",
          "publish": "Saving result"}


def size_text(value):
    return f"{value / 1_000_000:.2f} MB" if value is not None else "—"


class MainWindow(QMainWindow):
    """Translate presentation independently from stable core event data."""

    def __init__(self, preferences=None, log_path=None):
        super().__init__()
        assets = Path(__file__).parent / "assets"
        QApplication.instance().setWindowIcon(QIcon(str(assets / "icon-big.ico")))
        self.setWindowIcon(QIcon(str(assets / "icon-small.png")))
        self.preferences = preferences or QSettings("koolakoff", "analog-fpv-compressor")
        self.session_log = EventLogger(log_path, echo=False)
        self.session_log.__enter__()
        self.translator = None
        self.thread = self.worker = self.cancel_token = None
        self.pending_close = False
        self.summary = None
        self.records = {}
        self.bindings = []
        self.combos = []
        self.log_events = deque(maxlen=300)
        self.last_progress = None
        self.last_selected = None
        self.setAcceptDrops(True)
        self.resize(1000, 760)
        self.setMinimumSize(740, 560)
        self.build_ui()
        language = self.preferences.value("language", system_language())
        self.language.setCurrentIndex(max(0, self.language.findData(language)))
        self.change_language()
        self.language.currentIndexChanged.connect(self.change_language)

    def bind(self, widget, source, setter="setText"):
        self.bindings.append((widget, source, setter))
        return widget

    def button(self, source, action):
        button = self.bind(QPushButton(), source)
        button.clicked.connect(action)
        return button

    def combo(self, choices):
        combo = QComboBox()
        for value, source in choices:
            combo.addItem(source, value)
        self.combos.append((combo, choices))
        return combo

    def form_row(self, form, source, widget):
        label = self.bind(QLabel(), source)
        label.setBuddy(widget)
        form.addRow(label, widget)
        return widget

    def build_ui(self):
        root = QWidget()
        layout = QVBoxLayout(root)
        layout.setContentsMargins(16, 12, 16, 12)
        toolbar = QHBoxLayout()
        self.add_button = self.button("Add files…", self.choose_files)
        self.remove_button = self.button("Remove selected", self.remove_selected)
        toolbar.addWidget(self.add_button)
        toolbar.addWidget(self.remove_button)
        toolbar.addStretch()
        toolbar.addWidget(QLabel("🌐"))
        self.language = QComboBox()
        for code, name in LANGUAGES.items():
            self.language.addItem(name, code)
        toolbar.addWidget(self.language)
        layout.addLayout(toolbar)
        self.queue = QTreeWidget()
        self.queue.setColumnCount(5)
        self.queue.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.queue.setRootIsDecorated(True)
        self.queue.setAlternatingRowColors(True)
        self.queue.setMinimumHeight(140)
        self.queue.setColumnWidth(0, 320)
        self.queue.setColumnWidth(1, 105)
        self.queue.setColumnWidth(2, 105)
        self.queue.setColumnWidth(3, 100)
        self.queue.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column in range(1, 5):
            self.queue.header().setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        self.queue.itemSelectionChanged.connect(self.update_actions)
        layout.addWidget(self.queue, 2)

        self.settings_panel = QWidget()
        settings_layout = QVBoxLayout(self.settings_panel)
        settings_layout.setContentsMargins(0, 0, 0, 0)
        self.output_group = self.bind(QGroupBox(), "Output", "setTitle")
        output_form = QFormLayout(self.output_group)
        self.output_form = output_form
        self.output_location = self.combo([("beside", "Beside each input"), ("folder", "Selected folder")])
        self.form_row(output_form, "Save to", self.output_location)
        folder_row = QWidget()
        self.folder_row = folder_row
        folder_layout = QHBoxLayout(folder_row)
        folder_layout.setContentsMargins(0, 0, 0, 0)
        self.output_dir = QLineEdit()
        self.folder_button = self.button("Browse…", self.choose_output_dir)
        folder_layout.addWidget(self.output_dir)
        folder_layout.addWidget(self.folder_button)
        self.form_row(output_form, "Folder", folder_row)
        self.output_location.currentIndexChanged.connect(self.update_dependencies)
        naming_row = QWidget()
        naming_layout = QHBoxLayout(naming_row)
        naming_layout.setContentsMargins(0, 0, 0, 0)
        self.suffix = QLineEdit("_converted")
        self.container = self.combo([("mkv", "MKV"), ("mp4", "MP4")])
        naming_layout.addWidget(self.suffix, 1)
        naming_layout.addWidget(self.bind(QLabel(), "Format"))
        naming_layout.addWidget(self.container)
        self.form_row(output_form, "Filename suffix", naming_row)
        settings_layout.addWidget(self.output_group)

        self.processing_group = self.bind(QGroupBox(), "Processing", "setTitle")
        processing_form = QFormLayout(self.processing_group)
        self.processing_form = processing_form
        self.denoise = self.form_row(processing_form, "Noise reduction", self.combo([
            ("auto", "Auto"), ("off", "Off"), ("weak", "Weak"), ("medium", "Medium"), ("strong", "Strong")]))
        self.scale = self.form_row(processing_form, "Resolution", self.combo([
            ("auto", "Auto"), ("original", "Original"), ("640x480", "640 × 480"),
            ("480x360", "480 × 360"), ("custom", "Custom")]))
        self.custom_scale = self.form_row(processing_form, "Custom resolution", QLineEdit("640x480"))
        self.scale.currentIndexChanged.connect(self.update_dependencies)
        self.snow = self.form_row(processing_form, "White noise", self.combo([
            ("remove", "Remove and join useful parts"), ("split", "Remove and split into files"),
            ("keep", "Keep")]))
        snow_mode = self.preferences.value("snow_mode", "split")
        snow_index = self.snow.findData(snow_mode)
        self.snow.setCurrentIndex(snow_index if snow_index >= 0 else self.snow.findData("split"))
        self.snow.currentIndexChanged.connect(
            lambda: self.preferences.setValue("snow_mode", self.snow.currentData()))
        self.audio = self.bind(QCheckBox(), "Keep audio")
        processing_form.addRow(self.audio)
        settings_layout.addWidget(self.processing_group)

        self.advanced_toggle = self.bind(QCheckBox(), "Advanced settings")
        settings_layout.addWidget(self.advanced_toggle)
        self.advanced_panel = QWidget()
        advanced_form = QFormLayout(self.advanced_panel)
        self.codec = self.form_row(advanced_form, "Codec", self.combo([("av1", "AV1"), ("hevc", "HEVC")]))
        self.rate_mode = self.form_row(advanced_form, "Rate control", self.combo([
            ("auto", "Auto"), ("crf", "CRF"), ("bitrate", "Target bitrate")]))
        self.crf = self.form_row(advanced_form, "CRF (lower means less compression)", QSpinBox())
        self.crf.setRange(0, 63)
        self.crf.setValue(48)
        self.bitrate = self.form_row(advanced_form, "Bitrate (bits/s, k or M)", QLineEdit("500k"))
        self.preset = self.form_row(advanced_form, "Encoder preset", QLineEdit("auto"))
        self.bind(self.preset, "Auto, AV1 0–13, or HEVC preset name", "setToolTip")
        self.deinterlace = self.form_row(advanced_form, "Deinterlace", self.combo([
            ("auto", "Auto"), ("off", "Off"), ("on", "On")]))
        self.field_order = self.form_row(advanced_form, "Field order", self.combo([
            ("auto", "Auto"), ("tff", "Top field first"), ("bff", "Bottom field first")]))
        self.snow_duration = self.form_row(advanced_form, "Minimum white-noise duration (s)", QLineEdit("auto"))
        self.bind(self.snow_duration, "Auto or a positive number of seconds", "setToolTip")
        self.threads = self.form_row(advanced_form, "CPU threads", QSpinBox())
        self.threads.setRange(1, 256)
        self.threads.setValue(4)
        tools_row = QWidget()
        tools_layout = QHBoxLayout(tools_row)
        tools_layout.setContentsMargins(0, 0, 0, 0)
        self.ffmpeg_dir = QLineEdit(str(self.preferences.value("ffmpeg_dir", "")))
        self.bind(self.ffmpeg_dir, "Leave empty for automatic tool discovery", "setPlaceholderText")
        tools_layout.addWidget(self.ffmpeg_dir)
        tools_layout.addWidget(self.button("Browse…", self.choose_ffmpeg_dir))
        self.form_row(advanced_form, "FFmpeg folder", tools_row)
        self.analyze_only = self.bind(QCheckBox(), "Analyze only (do not encode)")
        advanced_form.addRow(self.analyze_only)
        self.advanced_panel.setVisible(False)
        self.advanced_toggle.toggled.connect(self.advanced_panel.setVisible)
        self.rate_mode.currentIndexChanged.connect(self.update_dependencies)
        self.codec.currentIndexChanged.connect(self.update_dependencies)
        self.snow.currentIndexChanged.connect(self.update_dependencies)
        settings_layout.addWidget(self.advanced_panel)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.settings_panel)
        scroll.setMinimumHeight(200)
        layout.addWidget(scroll, 3)

        actions = QHBoxLayout()
        self.open_result = self.button("Open result", lambda: self.open_selected("output"))
        self.open_folder = self.button("Open folder", lambda: self.open_selected("folder"))
        for button in (self.open_result, self.open_folder):
            actions.addWidget(button)
        actions.addStretch()
        self.log_toggle = self.bind(QCheckBox(), "Processing log")
        actions.addWidget(self.log_toggle)
        self.open_log_button = self.bind(QPushButton(), "Open log")
        self.open_log_button.setToolTip(str(self.session_log.path))
        self.open_log_button.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.session_log.path))))
        actions.addWidget(self.open_log_button)
        layout.addLayout(actions)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(1500)
        self.log.setMaximumHeight(160)
        self.log.setVisible(False)
        self.log_toggle.toggled.connect(self.log.setVisible)
        layout.addWidget(self.log)
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.status)
        self.selected_label = QLabel()
        self.selected_label.setWordWrap(True)
        self.selected_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.selected_label.setVisible(False)
        layout.addWidget(self.selected_label)
        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setValue(0)
        layout.addWidget(self.progress)
        bottom = QHBoxLayout()
        self.hint = self.bind(QLabel(), "Drop files here or use Add files. Originals are preserved.")
        self.hint.setWordWrap(True)
        bottom.addWidget(self.hint, 1)
        self.stop_button = self.button("Stop processing", self.stop)
        self.start_button = self.button("Start processing", self.start)
        bottom.addWidget(self.stop_button)
        bottom.addWidget(self.start_button)
        layout.addLayout(bottom)
        self.setCentralWidget(root)
        QShortcut(QKeySequence("Ctrl+O"), self, activated=self.choose_files)
        QShortcut(QKeySequence("Delete"), self, activated=self.remove_selected)
        self.update_dependencies()
        self.update_actions()

    @Slot()
    def change_language(self):
        app = QApplication.instance()
        if self.translator is not None:
            app.removeTranslator(self.translator)
        code = self.language.currentData()
        self.translator = CatalogTranslator(code, self)
        app.installTranslator(self.translator)
        self.preferences.setValue("language", code)
        self.setWindowTitle(f"analog-fpv-compressor {__version__}")
        for widget, source, setter in self.bindings:
            getattr(widget, setter)(tr(source))
        for combo, choices in self.combos:
            combo.blockSignals(True)
            for index, (_, source) in enumerate(choices):
                combo.setItemText(index, tr(source))
            combo.blockSignals(False)
        self.language.setAccessibleName(tr("Language"))
        self.queue.setHeaderLabels([tr(source) for source in ("File", "Input size", "Output size", "Reduction", "Status")])
        for record in self.records.values():
            self.render_record(record)
        self.render_status()
        self.render_selected()
        self.render_log()

    def render_selected(self):
        if not self.last_selected:
            self.selected_label.setVisible(False)
            return
        selected = self.last_selected["selected"]
        values = [("Resolution", f"{selected['width']} × {selected['height']}"),
                  ("Noise reduction", tr({"off": "Off", "weak": "Weak", "medium": "Medium", "strong": "Strong"}.get(selected["denoise_level"], "Auto"))),
                  ("Deinterlace", tr("On" if selected["deinterlace"] else "Off")),
                  ("Codec", selected["codec"].upper()),
                  ("Target bitrate" if selected["bitrate"] else "CRF", str(selected["bitrate"] or selected["crf"]))]
        text = " · ".join(f"{tr(label)}: {value}" for label, value in values)
        text += " · " + tr("Audio kept" if selected["audio"] == "keep" else "Audio removed")
        self.selected_label.setText(text)
        self.selected_label.setToolTip(tr("Selected settings and explanations (English diagnostics):") + "\n" +
                                       json.dumps(self.last_selected.get("reasons", {}), ensure_ascii=False, indent=2))
        self.selected_label.setVisible(True)

    def render_record(self, record):
        item = record["item"]
        item.setText(4, tr(record["state"]) + (" · " + tr("Warning") if record.get("warnings") else ""))
        item.setText(1, size_text(record.get("source_bytes")))
        item.setText(2, size_text(record.get("bytes")))
        if record.get("bytes") is not None and record.get("source_bytes"):
            reduction = 100 * (1 - record["bytes"] / record["source_bytes"])
            item.setText(3, f"{reduction:.1f}%")
        for index in range(item.childCount()):
            child = item.child(index)
            child.setText(4, tr("Ready"))

    def render_status(self):
        if self.cancel_token and self.cancel_token.is_cancelled():
            self.status.setText(tr("Stopping… Completed results will be kept."))
        elif self.thread is not None and self.last_progress:
            data = self.last_progress
            source = STAGES.get(data.get("stage"), "Processing")
            text = tr("File {index} of {count} · {stage}", index=data.get("job_index", 1),
                      count=data.get("job_count", len(self.records)), stage=tr(source))
            if "part_index" in data:
                text += " · " + tr("Part {index} of {count}", index=data["part_index"], count=data["part_count"])
            self.status.setText(text)
        elif self.summary:
            key = "Stopped · ready: {succeeded} · errors: {failed}" if self.summary["cancelled"] else "Finished · ready: {succeeded} · errors: {failed}"
            self.status.setText(tr(key, **self.summary))
        else:
            self.status.setText(tr("Ready to process"))

    def render_log(self):
        lines = []
        for event in self.log_events:
            data = event.data
            name = Path(data["input"]).name if data.get("input") else ""
            labels = {"job.started": "Started", "job.completed": "Ready", "job.analyzed": "Analyzed",
                      "job.failed": "Failed", "batch.failed": "Failed", "job.cancelled": "Stopped",
                      "plan.resolved": "Selected settings", "warning": "Warning",
                      "flights.planned": "Parts planned", "part.completed": "Part ready"}
            if event.code in labels:
                lines.append(f"{name} · {tr(labels[event.code])}")
                if event.code == "plan.resolved":
                    lines.append(tr("Selected settings and explanations (English diagnostics):"))
                    lines.append(json.dumps(data.get("selected", {}), ensure_ascii=False, default=str))
                elif "message" in data:
                    lines.append(tr("Diagnostic details (English):") + " " + str(data["message"]))
        self.log.setPlainText("\n".join(lines))
        self.log.verticalScrollBar().setValue(self.log.verticalScrollBar().maximum())

    def add_paths(self, paths):
        if self.thread is not None:
            return
        for path in paths:
            source = Path(path).resolve()
            if not source.is_file() or str(source) in self.records:
                continue
            item = QTreeWidgetItem([source.name])
            item.setToolTip(0, str(source))
            item.setData(0, Qt.ItemDataRole.UserRole, {"input": str(source)})
            self.queue.addTopLevelItem(item)
            record = {"item": item, "source_bytes": source.stat().st_size, "state": "Queued", "bytes": None}
            self.records[str(source)] = record
            self.render_record(record)
        self.update_actions()

    def choose_files(self):
        if self.thread is None:
            directory = str(self.preferences.value("last_input_dir", ""))
            if directory and not Path(directory).is_dir():
                directory = ""
            paths, _ = QFileDialog.getOpenFileNames(self, tr("Add files…"), directory, tr("Video files (*.avi *.mkv *.mp4 *.mov);;All files (*)"))
            if paths:
                self.preferences.setValue("last_input_dir", str(Path(paths[0]).resolve().parent))
            self.add_paths(paths)

    def remove_selected(self):
        if self.thread is not None:
            return
        for item in self.queue.selectedItems():
            if item.parent() is None:
                self.records.pop(item.data(0, Qt.ItemDataRole.UserRole)["input"], None)
                self.queue.takeTopLevelItem(self.queue.indexOfTopLevelItem(item))
        self.update_actions()

    def choose_output_dir(self):
        path = QFileDialog.getExistingDirectory(self, tr("Output folder"), self.output_dir.text())
        if path:
            self.output_dir.setText(path)

    def choose_ffmpeg_dir(self):
        path = QFileDialog.getExistingDirectory(self, tr("FFmpeg folder"), self.ffmpeg_dir.text())
        if path:
            self.ffmpeg_dir.setText(path)

    def update_dependencies(self):
        folder = self.output_location.currentData() == "folder"
        self.output_form.setRowVisible(self.folder_row, folder)
        self.output_dir.setEnabled(folder)
        self.folder_button.setEnabled(folder)
        self.custom_scale.setEnabled(self.scale.currentData() == "custom")
        self.processing_form.setRowVisible(self.custom_scale, self.scale.currentData() == "custom")
        self.crf.setMaximum(63 if self.codec.currentData() == "av1" else 51)
        self.crf.setEnabled(self.rate_mode.currentData() == "crf")
        self.bitrate.setEnabled(self.rate_mode.currentData() == "bitrate")
        self.snow_duration.setEnabled(self.snow.currentData() != "keep")

    def options(self):
        mode = self.rate_mode.currentData()
        duration = self.snow_duration.text().strip()
        if duration != "auto":
            try:
                duration = float(duration.replace(",", "."))
            except ValueError:
                raise ValueError(tr("White-noise duration must be auto or a positive number.")) from None
        if self.output_location.currentData() == "folder" and not self.output_dir.text().strip():
            raise ValueError(tr("Choose an output folder."))
        scale = self.custom_scale.text().strip() if self.scale.currentData() == "custom" else self.scale.currentData()
        return {"output_dir": self.output_dir.text().strip() if self.output_location.currentData() == "folder" else None,
                "output_suffix": self.suffix.text(), "output_format": self.container.currentData(),
                "denoise": self.denoise.currentData(), "scale": scale,
                "cut_no_signal": "off" if self.snow.currentData() == "keep" else "auto",
                "split_flights": self.snow.currentData() == "split", "audio": "keep" if self.audio.isChecked() else "remove",
                "codec": self.codec.currentData(), "crf": self.crf.value() if mode == "crf" else "auto",
                "bitrate": self.bitrate.text().strip() if mode == "bitrate" else "auto",
                "preset": self.preset.text().strip(), "deinterlace": self.deinterlace.currentData(),
                "field_order": self.field_order.currentData(), "no_signal_min_duration": duration,
                "threads": self.threads.value(), "ffmpeg_dir": self.ffmpeg_dir.text().strip() or None,
                "analyze_only": self.analyze_only.isChecked()}

    @Slot()
    def start(self):
        if self.thread is not None or not self.records:
            return
        try:
            options = self.options()
        except ValueError as error:
            QMessageBox.warning(self, tr("Invalid settings"), str(error))
            return
        self.preferences.setValue("ffmpeg_dir", self.ffmpeg_dir.text().strip())
        self.summary = self.last_progress = None
        self.last_selected = None
        self.render_selected()
        self.log_events.clear()
        self.render_log()
        for record in self.records.values():
            record["state"] = "Queued"
            record["bytes"] = None
            record["warnings"] = []
            record.pop("output", None)
            record.pop("report", None)
            record.pop("expected_report", None)
            record["item"].takeChildren()
            record["item"].setToolTip(4, "")
            record["item"].setText(3, "—")
            self.render_record(record)
        self.cancel_token = CancelToken()
        self.thread = QThread(self)
        self.worker = BatchWorker(self.records, options, self.cancel_token, session_log=self.session_log)
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.event.connect(self.receive_event)
        self.worker.finished.connect(self.receive_summary)
        self.worker.finished.connect(self.thread.quit)
        self.worker.finished.connect(self.worker.deleteLater)
        self.thread.finished.connect(self.thread_finished)
        self.thread.finished.connect(self.thread.deleteLater)
        self.settings_panel.setEnabled(False)
        self.progress.setRange(0, 0)
        self.update_actions()
        self.thread.start()

    @Slot(object)
    def receive_event(self, event):
        data = event.data
        record = self.records.get(data.get("input"))
        if event.code == "progress":
            self.last_progress = data
            fraction = data.get("fraction")
            self.progress.setRange(0, 0 if fraction is None else 1000)
            if fraction is not None:
                self.progress.setValue(round(fraction * 1000))
            self.render_status()
            return
        if record:
            if event.code == "warning":
                record.setdefault("warnings", []).append(str(data.get("message", "")))
                record["item"].setToolTip(4, "\n".join(record["warnings"]))
            if event.code == "plan.resolved":
                self.last_selected = data
                self.render_selected()
            states = {"job.started": "Processing", "job.completed": "Ready", "job.analyzed": "Analyzed",
                      "job.failed": "Failed", "job.cancelled": "Stopped"}
            if event.code in states:
                record["state"] = states[event.code]
            if event.code in ("job.completed", "job.analyzed"):
                if "bytes" in data:
                    record["bytes"] = data["bytes"]
            if event.code == "processing_completed":
                output = str(data["output_path"])
                if "part_index" in data:
                    record["bytes"] = (record["bytes"] or 0) + data["bytes"]
                    child = QTreeWidgetItem([Path(output).name, "—", size_text(data["bytes"]), "—", tr("Ready")])
                    child.setData(0, Qt.ItemDataRole.UserRole, {"output": output,
                                                              "input": data["input"]})
                    child.setToolTip(0, output)
                    record["item"].addChild(child)
                    record["item"].setExpanded(True)
                else:
                    record["output"] = output
                    record["bytes"] = data["bytes"]
            if event.code in ("job.failed", "job.cancelled"):
                record["item"].setToolTip(4, str(data.get("message", "")))
            self.render_record(record)
        if event.code == "batch.failed":
            for pending in self.records.values():
                if pending["state"] == "Queued":
                    pending["state"] = "Not processed"
                    self.render_record(pending)
            self.log_toggle.setChecked(True)
        if event.code == "job.failed":
            self.log_toggle.setChecked(True)
        self.log_events.append(event)
        self.render_log()
        self.update_actions()

    @Slot(object)
    def receive_summary(self, summary):
        self.summary = summary

    @Slot()
    def thread_finished(self):
        self.thread = self.worker = None
        self.cancel_token = None
        if self.summary and self.summary["cancelled"]:
            for record in self.records.values():
                if record["state"] in ("Queued", "Processing"):
                    record["state"] = "Stopped"
                    self.render_record(record)
        self.settings_panel.setEnabled(True)
        self.progress.setRange(0, 1000)
        self.progress.setValue(1000 if self.summary and not self.summary["failed"] and not self.summary["cancelled"] else 0)
        self.render_status()
        self.update_actions()
        if self.pending_close:
            self.close()

    def stop(self):
        if self.cancel_token:
            self.cancel_token.cancel()
            self.stop_button.setEnabled(False)
            self.render_status()

    def selection_paths(self):
        item = self.queue.currentItem()
        if item is None:
            return {}
        data = dict(item.data(0, Qt.ItemDataRole.UserRole) or {})
        if item.parent() is None:
            record = self.records[data["input"]]
            data.update({key: record[key] for key in ("output",) if record.get(key)})
        return data

    def update_actions(self):
        busy = self.thread is not None
        self.add_button.setEnabled(not busy)
        self.remove_button.setEnabled(not busy and bool(self.queue.selectedItems()))
        self.start_button.setEnabled(not busy and bool(self.records))
        self.stop_button.setEnabled(busy and not (self.cancel_token and self.cancel_token.is_cancelled()))
        paths = self.selection_paths()
        self.open_result.setEnabled(bool(paths.get("output") and Path(paths["output"]).is_file()))
        self.open_folder.setEnabled(bool(paths.get("output")))

    def open_selected(self, kind):
        paths = self.selection_paths()
        path = paths.get("output" if kind == "folder" else kind)
        if path:
            target = Path(path).parent if kind == "folder" else Path(path)
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(target)))

    def dragEnterEvent(self, event):
        if self.thread is None and event.mimeData().hasUrls() and any(url.isLocalFile() for url in event.mimeData().urls()):
            event.acceptProposedAction()

    def dropEvent(self, event):
        self.add_paths([url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()])
        event.acceptProposedAction()

    def closeEvent(self, event):
        if self.thread is not None:
            prompt = QMessageBox(QMessageBox.Icon.Question, tr("Stop processing?"),
                                 tr("Stop the queue and close after cleanup? Completed files will be kept."),
                                 QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, self)
            prompt.button(QMessageBox.StandardButton.Yes).setText(tr("Yes"))
            prompt.button(QMessageBox.StandardButton.No).setText(tr("No"))
            prompt.setDefaultButton(QMessageBox.StandardButton.No)
            answer = prompt.exec()
            event.ignore()
            if answer == QMessageBox.StandardButton.Yes:
                self.pending_close = True
                self.stop()
        else:
            if self.session_log.text_file is not None:
                self.session_log.__exit__(None, None, None)
            if self.translator:
                QApplication.instance().removeTranslator(self.translator)
            event.accept()


def launch():
    """Use the platform's standard widget style and system font."""
    app = QApplication(sys.argv)
    app.setApplicationName("analog-fpv-compressor")
    app.setOrganizationName("koolakoff")
    try:
        window = MainWindow()
    except OSError as error:
        translator = CatalogTranslator(system_language())
        app.installTranslator(translator)
        QMessageBox.critical(None, tr("Cannot create session log"),
                             tr("The program folder must be writable.\n{error}", error=error))
        return 1
    previous_hook = sys.excepthook
    def log_exception(kind, value, tb):
        import traceback
        from ..models import Event
        if window.session_log.text_file is not None:
            window.session_log(Event("application.error", {"traceback": "".join(traceback.format_exception(kind, value, tb))}))
        previous_hook(kind, value, tb)
    sys.excepthook = log_exception
    window.show()
    try:
        return app.exec()
    finally:
        sys.excepthook = previous_hook
        if window.session_log.text_file is not None:
            window.session_log.__exit__(None, None, None)
