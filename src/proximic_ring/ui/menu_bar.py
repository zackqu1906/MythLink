"""Compact macOS menu-bar connection and battery indicators."""
from __future__ import annotations

import weakref

from PySide6.QtCore import QBuffer, QIODevice, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPen

_target_class = None


def _menu_target_class():
    global _target_class
    if _target_class is None:
        from Foundation import NSObject

        class ProxiMicMenuBarTarget(NSObject):
            def invoke_(target, sender):
                owner = target.owner()
                if owner is None:
                    return
                index = sender.tag()
                if 0 <= index < len(owner._actions):
                    action = owner._actions[index]
                    if action.isEnabled():
                        action.trigger()

        _target_class = ProxiMicMenuBarTarget
    return _target_class


def connection_title(connected, battery_available, battery_percentage, charging=False):
    if not connected:
        return "未连接"
    title = "已连接"
    if battery_available and type(battery_percentage) is int and 0 <= battery_percentage <= 100:
        title += f" · {battery_percentage}%"
        if charging:
            title += " 充电"
    return title


def status_image(icon, connected, percentage, charging=False, show_info=True):
    """44 × 22 points: application icon, then a dot above a QML-style battery."""
    image = QImage(88 if show_info else 36, 44, QImage.Format_ARGB32_Premultiplied)
    image.setDevicePixelRatio(2)
    image.fill(Qt.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.Antialiasing)
    icon.paint(painter, 0, 2, 18, 18)
    if not show_info:
        painter.end()
        return image
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor("#8AA4FF" if connected else "#FF7B86"))
    painter.drawEllipse(QRectF(29, 1, 6, 6))
    known = connected and percentage is not None
    color = QColor("#647085" if not known else "#FF7B86" if percentage <= 15
                   else "#F5B942" if percentage <= 35 else "#4DD4AC")
    painter.setPen(QPen(color, 1))
    painter.setBrush(Qt.NoBrush)
    painter.drawRoundedRect(QRectF(22.5, 11.5, 18, 10), 2, 2)
    painter.setPen(Qt.NoPen)
    painter.setBrush(color)
    painter.drawRoundedRect(QRectF(41.5, 14, 2, 5), 1, 1)
    if known and percentage > 0:
        painter.drawRoundedRect(QRectF(24.5, 13.5, max(1, 14 * percentage / 100), 6), 1, 1)
    elif not known:
        painter.drawRoundedRect(QRectF(29, 16, 5, 1), 0.5, 0.5)
    if known and charging:
        # A small bolt remains within the battery; no extra text or width.
        from PySide6.QtGui import QPainterPath

        bolt = QPainterPath()
        bolt.moveTo(33, 12.5)
        for x, y in [(29.5, 16.8), (32, 16.8), (30.5, 20.5), (35, 15.5), (32.5, 15.5)]:
            bolt.lineTo(x, y)
        bolt.closeSubpath()
        painter.setPen(QPen(QColor("#263247"), 0.5))
        painter.setBrush(QColor("#FFFFFF"))
        painter.drawPath(bolt)
    painter.end()
    return image


class MacMenuBar:
    """Own exactly one NSStatusItem, with the host's existing QAction callbacks."""

    def __init__(self, icon):
        import AppKit
        self._kit = AppKit
        self._icon = icon
        self._item = None
        self._actions = []
        self._last_state = None
        self._last_show_info = None
        self._tooltip = "ProxiMic Voice"

        self._target = _menu_target_class().alloc().init()
        self._target.owner = weakref.ref(self)
        try:
            self._bar = AppKit.NSStatusBar.systemStatusBar()
            self._item = self._bar.statusItemWithLength_(AppKit.NSVariableStatusItemLength)
            button = self._item.button()
            if button is None:
                raise RuntimeError("macOS 未提供菜单栏按钮")
            button.setImagePosition_(AppKit.NSImageOnly)
            button.setTitle_("")
            button.setAccessibilityLabel_("ProxiMic Voice")
            self.setConnectionStatus(False, False, -1, False)
        except Exception:
            self.hide()
            raise

    def setContextMenu(self, menu):
        self._qt_menu = menu  # Keep QActions alive for native callbacks.
        self._actions = list(menu.actions())
        self._menu = self._kit.NSMenu.alloc().initWithTitle_("ProxiMic Voice")
        self._menu.setAutoenablesItems_(False)
        self._entries = []
        for index, action in enumerate(self._actions):
            if action.isSeparator():
                entry = self._kit.NSMenuItem.separatorItem()
            else:
                entry = self._kit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(action.text(), "invoke:", "")
                entry.setTarget_(self._target)
                entry.setTag_(index)
            self._entries.append(entry)
            self._menu.addItem_(entry)
        self._item.setMenu_(self._menu)
        self._sync_menu()

    def _sync_menu(self):
        for action, entry in zip(self._actions, self._entries):
            if not action.isSeparator():
                entry.setTitle_(action.text())
                entry.setEnabled_(action.isEnabled())

    def setToolTip(self, text):
        self._tooltip = text
        if self._item is not None:
            self._update_tooltip()
            self._sync_menu()

    def _update_tooltip(self):
        connected, percentage, charging = self._last_state
        detail = connection_title(connected, percentage is not None, percentage, charging)
        if connected and percentage is None:
            detail += " · 电量未知"
        self._item.button().setToolTip_(self._tooltip + "\n" + detail)
        self._item.button().setAccessibilityLabel_("ProxiMic Voice · " + detail)

    def _make_status_image(self, connected, percentage, charging, show_info):
        from Foundation import NSData

        buffer = QBuffer()
        buffer.open(QIODevice.WriteOnly)
        if not status_image(self._icon, connected, percentage, charging, show_info).save(buffer, "PNG"):
            raise RuntimeError("菜单栏图标转换失败")
        data = bytes(buffer.data())
        image = self._kit.NSImage.alloc().initWithData_(NSData.dataWithBytes_length_(data, len(data)))
        if image is None:
            raise RuntimeError("菜单栏图标加载失败")
        image.setSize_((44 if show_info else 18, 22))
        image.setTemplate_(False)
        return image

    def setConnectionStatus(self, connected, available, percentage, charging, show_info=True):
        valid = connected and available and type(percentage) is int and 0 <= percentage <= 100
        state = (bool(connected), percentage if valid else None, bool(charging and valid))
        if self._item is not None and (state != self._last_state or show_info != self._last_show_info):
            self._image = self._make_status_image(*state, show_info)
            self._item.button().setImage_(self._image)
            self._last_state = state
            self._last_show_info = show_info
            self._update_tooltip()

    def show(self):
        if self._item is not None:
            self._item.setVisible_(True)

    def hide(self):
        if self._item is not None:
            self._bar.removeStatusItem_(self._item)
            self._item = None
        self._actions = []
        if hasattr(self, "_entries"):
            for entry in self._entries:
                if not entry.isSeparatorItem():
                    entry.setTarget_(None)
            self._entries = []
