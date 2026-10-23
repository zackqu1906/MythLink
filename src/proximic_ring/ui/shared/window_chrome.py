"""Finish the macOS native titlebar while retaining the standard window buttons."""
import sys


def configure_main_window_chrome(window) -> None:
    if sys.platform != "darwin":
        return

    from PySide6.QtGui import QGuiApplication

    if QGuiApplication.platformName() != "cocoa":
        return

    import ctypes
    import AppKit
    import objc

    # Qt's expanded client area keeps the NSWindow and native traffic lights.
    # Hide just the centered title; window.title still identifies it to macOS.
    view = objc.objc_object(c_void_p=ctypes.c_void_p(int(window.winId())))
    native = view.window()
    native.setTitleVisibility_(AppKit.NSWindowTitleHidden)
    native.setTitlebarSeparatorStyle_(AppKit.NSTitlebarSeparatorStyleNone)
