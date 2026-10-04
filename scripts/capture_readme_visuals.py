"""Compose localized README illustrations from real, frame-aligned DVR samples.

Filtering is shown before video encoding. The timeline uses real source frames
in a schematic layout; it does not simulate an application editor screenshot.
"""

import hashlib
import json
from pathlib import Path
import subprocess

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen
from PySide6.QtWidgets import QApplication

from analog_fpv_compressor.runtime import discover_tools

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "outputs/readme-visuals"
DESTINATION = ROOT / "docs/images"
SKY_DETAIL = (180, 90, 270, 120)


def frame_at(source, target, tools, name, denoise=False):
    """Select one native decoded frame; warm up the denoiser before selection."""
    times_file = EVIDENCE / (source.stem + ".times.json")
    if not times_file.exists():
        data = subprocess.check_output([tools["ffprobe"], "-v", "error", "-threads", "2", "-select_streams", "v:0",
            "-show_frames", "-show_entries", "frame=best_effort_timestamp_time", "-of", "json", str(source)])
        times_file.write_bytes(data)
    frames = json.loads(times_file.read_bytes())["frames"]
    index = min(range(len(frames)), key=lambda i: abs(float(frames[i]["best_effort_timestamp_time"]) - target))
    instant = float(frames[index]["best_effort_timestamp_time"])
    path = EVIDENCE / (name + ".png")
    filters = (["hqdn3d=3:5:1.5:6"] if denoise else []) + [f"select=eq(n\\,{index})", "format=rgb24"]
    command = [tools["ffmpeg"], "-hide_banner", "-nostdin", "-v", "error", "-y", "-threads", "2", "-i", str(source),
               "-an", "-filter_threads", "2", "-vf", ",".join(filters), "-frames:v", "1", "-update", "1", str(path)]
    subprocess.run(command, check=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    print(f"Captured {name}: native frame {index}, {instant:.6f}s", flush=True)
    return QImage(str(path)), {"source": str(source.relative_to(ROOT)), "frame": index,
                             "time_s": instant, "command": command}


def canvas(height):
    image = QImage(1200, height, QImage.Format.Format_RGB32)
    image.fill(QColor("#f8fafc"))
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
    return image, painter


def label(painter, x, y, width, height, text, size=18, color="#0f172a", bold=False):
    font = QFont("Segoe UI")
    font.setPixelSize(size)
    font.setBold(bold)
    painter.setFont(font)
    painter.setPen(QColor(color))
    painter.drawText(QRectF(x, y, width, height), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, text)


def save(image, painter, name):
    painter.end()
    if not image.save(str(DESTINATION / name)):
        raise RuntimeError(f"Cannot save {name}")


def denoise_comparison(before, after, labels, language):
    full_height = 540 * before.height() / before.width()
    detail_label_y = 126 + full_height + 8
    detail_y = detail_label_y + 44
    image, painter = canvas(round(detail_y + SKY_DETAIL[3] * 2 + 36))
    label(painter, 40, 18, 1120, 46, labels["noise_title"], 30, bold=True)
    crop = QRectF(*SKY_DETAIL)
    for x, source, title in ((40, before, labels["off"]), (620, after, labels["on"])):
        label(painter, x, 78, 540, 34, title, 23, bold=True)
        full = QRectF(x, 126, 540, full_height)
        painter.drawImage(full, source)
        painter.setPen(QPen(QColor("#38bdf8"), 2))
        scale = 540 / source.width()
        painter.drawRect(QRectF(x + crop.x() * scale, 126 + crop.y() * scale,
                               crop.width() * scale, crop.height() * scale))
        label(painter, x, detail_label_y, 540, 32, labels["detail"], 17, "#475569")
        painter.drawImage(QRectF(x, detail_y, crop.width() * 2, crop.height() * 2), source, crop)
    save(image, painter, f"denoise-{language}.png")


def time_label(instant):
    minutes, seconds = divmod(round(instant), 60)
    return f"{minutes:02d}:{seconds:02d}"


def timeline(images, records, labels, language):
    image, painter = canvas(635)
    label(painter, 48, 18, 1104, 45, labels["timeline_title"], 30, bold=True)
    label(painter, 48, 73, 1104, 32, labels["input"], 21, bold=True)
    for index, source in enumerate(images):
        x = 48 + index * 186
        noise = index in (2, 3)
        color = "#be123c" if noise else "#047857"
        label(painter, x, 112, 174, 26, labels["snow" if noise else "live"], 17, color, True)
        painter.drawImage(QRectF(x, 145, 174, 130.5), source)
        painter.setPen(QPen(QColor(color), 2))
        painter.drawRect(QRectF(x, 145, 174, 130.5))
        label(painter, x, 279, 174, 25, time_label(records[index]["time_s"]), 16, "#475569")
    painter.fillRect(QRectF(420, 312, 360, 42), QColor("#ffe4e6"))
    label(painter, 430, 315, 345, 35, labels["removed"], 16, "#9f1239", True)
    painter.setPen(QPen(QColor("#be123c"), 3))
    painter.drawLine(600, 355, 600, 380)
    painter.drawLine(600, 380, 591, 370)
    painter.drawLine(600, 380, 609, 370)
    label(painter, 48, 390, 1104, 32, labels["output"], 21, bold=True)
    for x, indices, title in ((48, (0, 1), labels["part1"]), (792, (4, 5), labels["part2"])):
        label(painter, x, 431, 360, 30, title, 20, "#047857", True)
        for offset, index in enumerate(indices):
            painter.drawImage(QRectF(x + offset * 186, 473, 174, 130.5), images[index])
    save(image, painter, f"snow-timeline-{language}.png")


def main():
    app = QApplication([])
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    DESTINATION.mkdir(parents=True, exist_ok=True)
    labels = json.loads((ROOT / "scripts/readme-visual-labels.json").read_text(encoding="utf-8"))
    tools = discover_tools()
    sky = ROOT / "examples/air-school-stadion-oneflight.AVI"
    before, off_record = frame_at(sky, 144, tools, "sky-off")
    after, on_record = frame_at(sky, 144, tools, "sky-medium", denoise=True)
    source = ROOT / "examples/Frantisek.AVI"
    captures = [frame_at(source, instant, tools, f"timeline-{index}")
                for index, instant in enumerate((60, 70, 90, 100, 150, 160))]
    images, records = zip(*captures)
    for language, text in labels.items():
        denoise_comparison(before, after, text, language)
        timeline(images, records, text, language)
    fingerprints = {}
    for path in (sky, source):
        with path.open("rb") as stream:
            fingerprints[path.name] = hashlib.file_digest(stream, "sha256").hexdigest()
    metadata = {"denoise": [off_record, on_record], "timeline": records, "sha256": fingerprints,
                "denoise_stage": "Before video encoding; identical crop and no contrast enhancement",
                "denoise_detail": {"crop_xywh": SKY_DETAIL, "magnification": 2},
                "timeline_layout": "Schematic; thumbnails are real source frames and spacing is not a time scale"}
    (EVIDENCE / "provenance.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    app.quit()


if __name__ == "__main__":
    main()
