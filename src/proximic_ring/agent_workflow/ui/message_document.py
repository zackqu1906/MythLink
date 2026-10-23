"""A small, isolated bridge for the Agent's local message renderer.

Only message text, layout, copy and user-clicked links cross this bridge. Agent
commands, permissions, settings and credentials are never exposed to the page.
"""
from pathlib import Path
import re

from PySide6.QtCore import QObject, Property, Signal, Slot, QUrl, Qt, QCoreApplication
from PySide6.QtGui import QDesktopServices, QGuiApplication

ASSETS = Path(__file__).parent / 'rich_text'


def initialize_web_engine():
    """Call before QApplication in the production entrypoint."""
    from PySide6.QtWebEngineQuick import QtWebEngineQuick
    QCoreApplication.setAttribute(Qt.AA_ShareOpenGLContexts)
    QtWebEngineQuick.initialize()


def message_link(value, base=None):
    """No arbitrary application protocols or executable URL handlers."""
    if not isinstance(value, str) or not value.strip() or any(ord(c) < 32 for c in value):
        return QUrl()
    value = value.strip()
    if value.startswith('#'):
        return QUrl()
    if value.startswith('/') or not QUrl(value).scheme():
        # Codex file references may include a one-based line suffix. The OS
        # default viewer opens the file; do not claim it navigates to that line.
        path = re.sub(r':\d+(?::\d+)?$', '', value)
        from proximic_ring.runtime_paths import resource_root
        path = (Path(base or resource_root()) / path).resolve()
        return QUrl.fromLocalFile(str(path)) if path.is_file() else QUrl()
    url = QUrl(value)
    if not url.isValid():
        return QUrl()
    if url.scheme() in ('http', 'https') and url.host():
        return url
    if url.scheme() == 'mailto' and url.path():
        return url
    if url.scheme() == 'file':
        path = Path(re.sub(r':\d+(?::\d+)?$', '', url.toLocalFile()))
        return QUrl.fromLocalFile(str(path)) if path.is_file() else QUrl()
    if url.scheme() == 'codex' and url.host() in ('threads', 'review'):
        return url
    return QUrl()


class MessageDocument(QObject):
    changed = Signal()
    metricsChanged = Signal()
    scrolled = Signal(float)
    copyFinished = Signal(bool)
    linkFailed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._source = ''
        self._height = 0.0
        self._revision = 0
        self._ready = False
        self._profile = None

    @Property(str, notify=changed)
    def source(self): return self._source

    @Property(int, notify=changed)
    def revision(self): return self._revision

    @Property(float, notify=metricsChanged)
    def documentHeight(self): return self._height

    @Property(bool, notify=metricsChanged)
    def ready(self): return self._ready

    @Property(QUrl, constant=True)
    def pageUrl(self): return QUrl.fromLocalFile(str(ASSETS / 'index.html'))

    @Property(str, constant=True)
    def baseUrl(self):
        from proximic_ring.runtime_paths import resource_root
        return QUrl.fromLocalFile(str(resource_root()) + '/').toString()

    @Property(QObject, constant=True)
    def profile(self):
        if self._profile is None:
            self._profile = create_message_profile(self)
        return self._profile

    @Slot(str)
    def setSource(self, source):
        if source == self._source: return
        self._source = source
        self._revision += 1
        self.changed.emit()

    @Slot()
    def invalidate(self):
        self._ready = False
        self.metricsChanged.emit()

    @Slot(int, float)
    def measure(self, revision, height):
        if revision != self._revision or not 0 < height < 10_000_000: return
        self._height = height
        self._ready = True
        self.metricsChanged.emit()

    @Slot(int, float)
    def scroll(self, revision, position):
        if revision == self._revision and 0 <= position <= self._height:
            self.scrolled.emit(position)

    @Slot(str)
    def copy(self, text):
        QGuiApplication.clipboard().setText(text)
        self.copyFinished.emit(True)

    @Slot(str)
    def openLink(self, value):
        url = message_link(value)
        if url.isEmpty() or not QDesktopServices.openUrl(url):
            self.linkFailed.emit('无法打开此链接或文件')


def create_message_profile(parent):
    """No cookies, downloads, remote scripts, fetches or arbitrary local reads."""
    from PySide6.QtWebEngineCore import QWebEngineUrlRequestInterceptor, QWebEngineUrlRequestInfo
    from PySide6.QtWebEngineQuick import QQuickWebEngineProfile

    class AssetsAndImages(QWebEngineUrlRequestInterceptor):
        def interceptRequest(self, info):
            url = info.requestUrl()
            kind = info.resourceType()
            image = kind == QWebEngineUrlRequestInfo.ResourceTypeImage
            allowed = False
            if url.scheme() == 'file':
                path = Path(url.toLocalFile()).resolve()
                allowed = path.is_relative_to(ASSETS.resolve()) or (image and path.suffix.lower() in
                    {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.svg', '.avif', '.bmp', '.ico'})
            elif url.scheme() == 'qrc':
                allowed = url.path() == '/qtwebchannel/qwebchannel.js'
            elif image and url.scheme() in ('https', 'http', 'data'):
                allowed = True
            info.block(not allowed)

    profile = QQuickWebEngineProfile(parent)
    profile.setOffTheRecord(True)
    interceptor = AssetsAndImages(profile)
    profile.setUrlRequestInterceptor(interceptor)
    profile.downloadRequested.connect(lambda request: request.cancel())
    profile._message_interceptor = interceptor
    return profile
