"""Small transparent PNGs for the AppKit candidate palette's IPC boundary."""
import base64
from functools import lru_cache

from PySide6.QtCore import QByteArray, QBuffer, QIODevice, QRectF
from PySide6.QtGui import QImage, QPainter
from PySide6.QtSvg import QSvgRenderer


@lru_cache(maxsize=128)
def _png(source):
    renderer = QSvgRenderer(QByteArray(base64.b64decode(source.split(',', 1)[1])))
    if not renderer.isValid():
        return ''
    image = QImage(192, 192, QImage.Format_ARGB32_Premultiplied)
    image.fill(0)
    painter = QPainter(image)
    renderer.render(painter, QRectF(0, 0, 192, 192))
    painter.end()
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.WriteOnly)
    image.save(buffer, 'PNG')
    return base64.b64encode(bytes(data)).decode('ascii')


def native_glyph_images(visuals):
    return [dict(character=item['character'], png=_png(item['source']) if item['source'] else '',
                 status=item['status'], next=item.get('next') or '') for item in visuals[:6]]
