"""Export application icons from the original sidebar brand's vector paths.

Run with the UI Python environment. macOS also exports the native .icns file.
"""
from pathlib import Path
import re
import struct
import subprocess
import sys
import xml.etree.ElementTree as ET

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication, QImage, QPainter
from PySide6.QtSvg import QSvgRenderer


def main():
    root = Path(__file__).resolve().parents[1]
    assets = root / "src/proximic_ring/ui/assets"
    exports = root / "packaging/icons"
    exports.mkdir(parents=True, exist_ok=True)
    source = ET.parse(assets / "figma/mythlink-logo.svg").getroot()
    union = source.find(".//{http://www.w3.org/2000/svg}path[@id='Union']")
    # The first two closed contours are the symbol; the rest are the wordmark.
    contours = re.findall(r"M[^M]+", union.attrib["d"])
    assert len(contours) >= 3 and all(part.rstrip().endswith("Z") for part in contours[:2])
    mark = f'<path d="{"".join(contours[:2])}" fill="{union.attrib["fill"]}"/>'
    standalone = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="6.06 -4.27635 40 40">\n'
                  '<title>MythLink</title>\n' + mark + '\n</svg>\n')
    (assets / "mythlink-mark.svg").write_text(standalone)
    # Keep the legacy asset path consistent for any older icon consumers.
    (assets / "proximic.svg").write_text(standalone)
    (assets / "mythlink-app.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1024 1024">\n'
        '<title>MythLink</title>\n'
        '<rect x="80" y="80" width="864" height="864" rx="188" fill="#F8F9FC"/>\n'
        '<g transform="translate(512 512) scale(20) translate(-26.06 -15.72365)">\n'
        + mark + '\n</g>\n</svg>\n')

    app = QGuiApplication.instance() or QGuiApplication(["brand-icons", "-platform", "offscreen"])
    renderer = QSvgRenderer(str(assets / "mythlink-app.svg"))
    assert renderer.isValid()
    iconset = root / ".build/app-icons/MythLink.iconset"
    iconset.mkdir(parents=True, exist_ok=True)
    images = {}
    for size in (16, 24, 32, 48, 64, 128, 256, 512, 1024):
        image = QImage(size, size, QImage.Format_ARGB32_Premultiplied)
        image.fill(Qt.transparent)
        painter = QPainter(image)
        renderer.render(painter)
        painter.end()
        images[size] = image
    assert images[1024].save(str(assets / "mythlink-app.png"))
    for size in (16, 32, 128, 256, 512):
        for scale in (1, 2):
            suffix = "@2x" if scale == 2 else ""
            assert images[size * scale].save(str(iconset / f"icon_{size}x{size}{suffix}.png"))
    if sys.platform == "darwin":
        subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(exports / "mythlink.icns")], check=True)

    # Windows ICO supports PNG payloads, preserving alpha at every DPI size.
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice
    sizes = (16, 24, 32, 48, 64, 128, 256)
    offset = 6 + 16 * len(sizes)
    directory, payloads = [], []
    for size in sizes:
        data = QByteArray()
        buffer = QBuffer(data)
        assert buffer.open(QIODevice.WriteOnly) and images[size].save(buffer, "PNG")
        payload = bytes(data)
        directory.append(struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32, len(payload), offset))
        payloads.append(payload)
        offset += len(payload)
    (exports / "mythlink.ico").write_bytes(struct.pack("<HHH", 0, 1, len(sizes)) + b"".join(directory + payloads))
    print("Exported MythLink symbol and app icons from the original sidebar artwork.")


if __name__ == "__main__":
    main()
