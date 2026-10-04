"""Convert the supplied PNG to a Windows ICO using Qt, without a new dependency."""

from pathlib import Path
import struct

from PySide6.QtCore import QByteArray, QBuffer, QIODevice, Qt
from PySide6.QtGui import QImage

ASSETS = Path(__file__).resolve().parents[1] / "src/analog_fpv_compressor/gui/assets"


def main():
    image = QImage(str(ASSETS / "icon-big.png"))
    if image.isNull():
        raise ValueError("Cannot read icon-big.png")
    images = []
    for size in (16, 24, 32, 48, 64, 128, 256):
        scaled = image.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        square = QImage(size, size, QImage.Format.Format_ARGB32)
        square.fill(Qt.GlobalColor.transparent)
        from PySide6.QtGui import QPainter
        painter = QPainter(square)
        painter.drawImage((size-scaled.width())//2, (size-scaled.height())//2, scaled)
        painter.end()
        data = QByteArray()
        buffer = QBuffer(data)
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        if not square.save(buffer, "PNG"):
            raise ValueError("Cannot encode icon")
        images.append((size, bytes(data)))
    offset = 6 + 16 * len(images)
    entries = []
    for size, data in images:
        entries.append(struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32, len(data), offset))
        offset += len(data)
    (ASSETS / "icon-big.ico").write_bytes(struct.pack("<HHH", 0, 1, len(images)) + b"".join(entries) + b"".join(data for _, data in images))


if __name__ == "__main__":
    main()
