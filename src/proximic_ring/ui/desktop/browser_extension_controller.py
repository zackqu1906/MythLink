"""One setup surface for both browsers; no implicit browser permission grants."""
from pathlib import Path
import subprocess
import sys
import threading
import time

from PySide6.QtCore import QObject, Property, Signal, Slot, QTimer, QUrl, Qt
from PySide6.QtGui import QDesktopServices

from ...browser_bridge.install import (install_chrome, chrome_extension_state,
                                      open_safari_settings, safari_extension_state, store_url)
from ...browser_bridge.server import BridgeServer
from ..shared.clipboard import QtClipboardBridge


class BrowserExtensionController(QObject):
    changed = Signal()
    setupRequested = Signal(str)
    _safariStateReady = Signal(str)
    _chromeStateReady = Signal(str)
    _settingsOpened = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.server = None
        self._states = {}
        self._message = ""
        self._chrome_path = ""
        self._setup_browser = ""
        self._closed = False
        self._safari_state = "unknown"
        self._chrome_state = "unknown"
        self._chrome_checking = False
        self._chrome_checked_at = -float("inf")
        self._settings_opening = False
        self._safari_checking = False
        self._safari_checked_at = -float("inf")
        self._safariStateReady.connect(self._apply_safari_state, Qt.QueuedConnection)
        self._chromeStateReady.connect(self._apply_chrome_state, Qt.QueuedConnection)
        self._settingsOpened.connect(self._settings_opened, Qt.QueuedConnection)
        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self.refresh)

    def start(self):
        if self.server or sys.platform != "darwin":
            return
        try:
            self.server = BridgeServer()
            self.server.start()
            self._message = ""
            self._timer.start()
            # Refresh a previously installed host after the application moved
            # or updated. A fresh installation is initiated by the setup button.
            host = Path.home() / "Library/Application Support/Google/Chrome/NativeMessagingHosts/com.proximic.voice.browser.json"
            if host.is_file():
                install_chrome()
        except OSError:
            self._message = "浏览器连接服务无法启动，请退出其他 Mythlink 实例后重试。" if not self.server else "Chrome 连接配置更新失败，请重新点击连接。"
        self.changed.emit()
        self.refresh()

    def close(self):
        self._closed = True
        self._timer.stop()
        if self.server:
            self.server.close()
            self.server = None

    @Slot()
    def refresh(self):
        states = self.server.broker.status() if self.server else {}
        if states != self._states:
            self._states = states
            self.changed.emit()
        if (not self._closed and sys.platform == "darwin" and not self._chrome_checking
                and time.monotonic() - self._chrome_checked_at >= 5):
            self._chrome_checking = True
            self._chrome_checked_at = time.monotonic()
            def read_chrome():
                state = chrome_extension_state()
                if not self._closed:
                    self._chromeStateReady.emit(state)
            threading.Thread(target=read_chrome, name="ChromeExtensionState", daemon=True).start()
        if (not self._closed and sys.platform == "darwin" and not self._safari_checking
                and not states.get("safari", {}).get("connected")
                and time.monotonic() - self._safari_checked_at >= 5):
            self._safari_checking = True
            self._safari_checked_at = time.monotonic()
            def read():
                state = safari_extension_state()
                if not self._closed:
                    self._safariStateReady.emit(state)
            threading.Thread(target=read, name="SafariExtensionState", daemon=True).start()

    @Slot(str)
    def _apply_chrome_state(self, state):
        self._chrome_checking = False
        if not self._closed and state != self._chrome_state:
            self._chrome_state = state
            self.changed.emit()

    @Slot(str)
    def _apply_safari_state(self, state):
        self._safari_checking = False
        if not self._closed and state != self._safari_state:
            self._safari_state = state
            self.changed.emit()

    def status(self, browser):
        state = self._states.get(browser, {})
        if not state.get("connected"):
            return "未连接"
        if state.get("reason") == "browser_permission_required":
            return "已连接 · 待网站授权"
        if state.get("reason") == "browser_page_unavailable":
            return "已连接 · 当前页不可访问"
        return "已连接"

    @Property(str, notify=changed)
    def chromeStatus(self):
        if self.chromeConnected:
            return self.status("chrome")
        return {"enabled": "已安装 · 已启用 · 等待 Chrome 连接",
                "installed": "已安装 · 等待 Chrome 连接",
                "disabled": "已安装 · 请在 Chrome 扩展设置中启用",
                "not_installed": "未检测到安装 · 请安装到使用的 Chrome 个人资料中"}.get(
                    self._chrome_state, "未连接 · 安装状态暂无法确认")

    @Property(str, notify=changed)
    def safariStatus(self):
        if self._states.get("safari", {}).get("connected"):
            return self.status("safari")
        return {"enabled": "已安装 · 已启用 · 等待 Safari 连接",
                "disabled": "已安装 · 请在 Safari 扩展设置中启用",
                "unavailable": "扩展组件已安装 · Safari 暂未加载，请打开扩展设置确认",
                "component_missing": "扩展组件缺失，请使用完整安装包"}.get(
                    self._safari_state, "未连接 · Safari 启用状态暂无法确认")

    @Property(str, notify=changed)
    def chromeSummary(self):
        if self.chromeConnected:
            return "已连接"
        return {"installed": "已安装 · 待连接", "enabled": "已安装 · 待连接",
                "disabled": "已安装 · 待启用", "not_installed": "未安装"}.get(self._chrome_state, "待确认")

    @Property(bool, notify=changed)
    def chromeInstalled(self):
        return self.chromeConnected or self._chrome_state in {"installed", "enabled", "disabled"}

    @Property(bool, notify=changed)
    def chromeConnected(self): return bool(self._states.get("chrome", {}).get("connected"))

    @Property(bool, notify=changed)
    def safariConnected(self): return bool(self._states.get("safari", {}).get("connected"))

    @Property(str, notify=changed)
    def safariSummary(self):
        if self.safariConnected:
            return "已连接"
        return {"enabled": "已安装 · 待连接", "disabled": "已安装 · 待启用",
                "unavailable": "已安装 · 待加载", "component_missing": "组件缺失"}.get(self._safari_state, "待确认")

    @Property(bool, notify=changed)
    def chromeReady(self): return self._states.get("chrome", {}).get("connected", False) and self._states.get("chrome", {}).get("authorized", False)

    @Property(bool, notify=changed)
    def safariReady(self): return self._states.get("safari", {}).get("connected", False) and self._states.get("safari", {}).get("authorized", False)

    @Property(str, notify=changed)
    def message(self): return self._message

    @Property(str, notify=changed)
    def chromePath(self): return self._chrome_path

    @Property(bool, constant=True)
    def chromeDevelopment(self): return not bool(store_url())

    @Property(bool, notify=changed)
    def safariSettingsOpening(self): return self._settings_opening

    @Slot(str)
    def beginSetup(self, browser):
        # Opening the guide never switches applications or claims installation.
        self._message = ""
        self._setup_browser = browser
        self.changed.emit()

    @Slot()
    def connectChrome(self):
        self._setup_browser = "chrome"
        self.start()
        try:
            path = install_chrome()
            self._chrome_path = str(path)
            self._chrome_state = chrome_extension_state()
            # A record in another profile does not identify the profile that
            # opens here. Always retain the manager's Load unpacked entry.
            destination = store_url() or "chrome://extensions/"
            copied = self.copyChromePath() if not store_url() else False
            subprocess.run(["/usr/bin/open", "-b", "com.google.Chrome", destination], check=True, capture_output=True, timeout=5)
            self.setupRequested.emit("com.google.Chrome")
            self._message = ("扩展目录已复制。请按第 2 步加载；列表已有 Mythlink 时，启用或重新加载即可。" if not store_url() and copied else
                "扩展页已打开，但路径复制失败。请点击「显示扩展文件夹」，在加载时选择这个文件夹。" if not store_url() else
                "点击「添加至 Chrome」，安装后按提示允许网站访问。")
        except (OSError, subprocess.SubprocessError):
            self._message = "请确认已安装 Google Chrome，再点击连接。扩展文件夹准备完成后也可以手动加载。"
        self.changed.emit()

    @Slot()
    def revealChrome(self):
        if self._chrome_path:
            QDesktopServices.openUrl(QUrl.fromLocalFile(self._chrome_path))

    def _copy_text(self, value):
        try:
            # Use the existing eager macOS pasteboard writer. QClipboard alone
            # can leave the previous item visible when Chrome takes focus.
            QtClipboardBridge().set_text(value)
            return True
        except (OSError, RuntimeError):
            return False

    @Slot(result=bool)
    def copyChromePath(self):
        copied = bool(self._chrome_path) and self._copy_text(self._chrome_path)
        self._message = ("扩展目录已复制。在 Chrome 的文件夹选择器中按 ⌘⇧G，再按 ⌘V 粘贴。" if copied else
                         "路径复制失败，请点击「显示扩展文件夹」找到要加载的目录。")
        self.changed.emit()
        return copied

    @Slot()
    def copyChromeExtensionsAddress(self):
        copied = self._copy_text("chrome://extensions/")
        self._message = ("扩展页地址已复制。请在 Chrome 地址栏粘贴并按回车。" if copied else
                         "地址复制失败，请在 Chrome 地址栏输入 chrome://extensions/。")
        self.changed.emit()

    @Slot()
    def connectSafari(self):
        self._setup_browser = "safari"
        self.start()
        if self._settings_opening:
            return
        self._settings_opening = True
        self._message = "正在打开 Safari 扩展设置…"
        def open_settings():
            try:
                open_safari_settings()
                success = True
            except (OSError, ValueError, subprocess.SubprocessError):
                success = False
            if not self._closed:
                self._settingsOpened.emit(success)
        threading.Thread(target=open_settings, name="SafariExtensionSettings", daemon=True).start()
        self.changed.emit()

    @Slot(bool)
    def _settings_opened(self, success):
        self._settings_opening = False
        if self._closed:
            return
        self._safari_checked_at = -float("inf")
        if success:
            self.setupRequested.emit("com.apple.Safari")
        if self._setup_browser != "safari":
            self.changed.emit()
            return
        self._message = ("已打开 Safari 扩展设置。选择「Mythlink 浏览器助手」，按需启用并允许网站访问。" if success else
                         "Safari 扩展设置打开失败。请在 Safari → 设置 → 扩展中查看「Mythlink 浏览器助手」；状态检查失败不代表未安装。")
        if success and self._safari_state == "unavailable":
            self._message += " 列表为空时，可展开「扩展列表里没有 Mythlink？」查看启用步骤。"
        self.changed.emit()
