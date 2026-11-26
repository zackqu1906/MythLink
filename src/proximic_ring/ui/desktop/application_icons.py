"""Load original application icons asynchronously for the application picker."""
from collections import OrderedDict
from pathlib import Path
import sys
from urllib.parse import unquote

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage
from PySide6.QtQuick import QQuickImageProvider


def native_application_icon(bundle, path):
    if bundle == 'com.proximic.voice':
        return QImage(str(Path(__file__).parents[1] / 'assets/mythlink-app.png'))
    if sys.platform == 'win32':
        import ctypes
        from ctypes import wintypes as w
        class FileInfo(ctypes.Structure):
            _fields_ = [('icon', w.HICON), ('index', ctypes.c_int), ('attributes', w.DWORD),
                        ('display', w.WCHAR * 260), ('typeName', w.WCHAR * 80)]
        info = FileInfo()
        shell = ctypes.windll.shell32
        pidl = ctypes.c_void_p()
        try:
            if path.startswith("shell:AppsFolder") and shell.SHParseDisplayName(path, None, ctypes.byref(pidl), 0, None) == 0:
                shell.SHGetFileInfoW(pidl, 0, ctypes.byref(info), ctypes.sizeof(info), 0x108)
            elif path and Path(path).is_file():
                shell.SHGetFileInfoW(path, 0, ctypes.byref(info), ctypes.sizeof(info), 0x100)
            if not info.icon:
                shell.SHGetFileInfoW("application.exe", 0x80, ctypes.byref(info), ctypes.sizeof(info), 0x110)
            return QImage.fromHICON(info.icon) if info.icon else QImage()
        finally:
            if info.icon:
                ctypes.windll.user32.DestroyIcon(w.HICON(info.icon))
            if pidl:
                ctypes.windll.ole32.CoTaskMemFree(pidl)
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
