"""Load original application icons asynchronously for the application picker."""
from collections import OrderedDict
from pathlib import Path
import sys
from urllib.parse import unquote

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage
from PySide6.QtQuick import QQuickImageProvider


def native_application_icon(bundle, path):
    if sys.platform != 'darwin':
        return QImage()
    import AppKit
    import objc
    with objc.autorelease_pool():
        workspace = AppKit.NSWorkspace.sharedWorkspace()
        location = Path(path) if path else None
        if location is None or not location.is_absolute() or location.suffix.casefold() != '.app' or not location.is_dir():
            url = workspace.URLForApplicationWithBundleIdentifier_(bundle)
            location = Path(str(url.path())) if url else None
        icon = workspace.iconForFile_(str(location)) if location else workspace.iconForFileType_('app')
        if icon is None:
            return QImage()
        rep = AppKit.NSBitmapImageRep.imageRepWithData_(icon.TIFFRepresentation())
        data = rep.representationUsingType_properties_(AppKit.NSBitmapImageFileTypePNG, {})
        return QImage.fromData(bytes(data), 'PNG').scaled(128, 128, Qt.KeepAspectRatio, Qt.SmoothTransformation)


class ApplicationIcons(QQuickImageProvider):
    def __init__(self):
        super().__init__(QQuickImageProvider.Image, QQuickImageProvider.ForceAsynchronousImageLoading)
        self.images = OrderedDict()

    def requestImage(self, identifier, size, requestedSize):
        if identifier not in self.images:
            bundle, _, path = identifier.partition('/')
            try:
                image = native_application_icon(unquote(bundle), unquote(path))
            except Exception:
                image = QImage()
            self.images[identifier] = image
            while len(self.images) > 256:
                self.images.popitem(last=False)
        image = self.images[identifier]
        size.setWidth(image.width())
        size.setHeight(image.height())
        return image


def install_application_icons(engine):
    engine.addImageProvider('applicationIcons', ApplicationIcons())
