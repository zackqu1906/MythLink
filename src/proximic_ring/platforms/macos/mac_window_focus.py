"""Native window focus policy adapted from Wails, with a Qt native-view adapter.

Source: wailsapp/wails, commit 5b8e87bd45c1b87d6f2cfb7c0e5c3176eda85a2a,
v3/pkg/application/webview_window_darwin.go (windowFocus).

MIT License
Copyright (c) 2018-Present Lea Anthony

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""
import ctypes

import objc
from AppKit import NSApplication, NSPanel, NSWindowStyleMaskNonactivatingPanel


def focus_window(window):
    """Explicit selection only: panels become key; normal windows activate the app."""
    native = objc.objc_object(c_void_p=ctypes.c_void_p(int(window.winId()))).window()
    if (native.isKindOfClass_(NSPanel)
            and int(native.styleMask()) & NSWindowStyleMaskNonactivatingPanel):
        native.orderFrontRegardless()
        native.makeKeyWindow()
        # QNSPanel's native key state alone does not update QWindow's active
        # state. Keep Qt's input context in sync after selecting this panel.
        window.requestActivate()
        return
    application = NSApplication.sharedApplication()
    # Qt nonactivating panels can report an active application while a previous
    # app-activation request is still in flight. This explicit user selection
    # must supersede that request even when AppKit's isActive flag is true.
    application.activateIgnoringOtherApps_(True)
    native.makeKeyAndOrderFront_(None)
    native.makeKeyWindow()
