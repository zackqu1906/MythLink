"""Give only the main macOS UI a Dock icon; workers must stay background-only."""
from __future__ import annotations

import sys


def configure_app_activation(*, background: bool) -> None:
    """Call on the process's main thread, before loading its UI or worker.

    The frozen bundle starts with LSUIElement, so even its bootloader does not
    add a temporary Dock icon. Only the normal Qt host promotes itself to a
    regular application. This does not activate it or request any permission.
    """
    if sys.platform != "darwin":
        return
    from AppKit import (
        NSApplication, NSApplicationActivationPolicyAccessory,
        NSApplicationActivationPolicyRegular,
    )

    policy = (NSApplicationActivationPolicyAccessory if background
              else NSApplicationActivationPolicyRegular)
    app = NSApplication.sharedApplication()
    # A frozen LSUIElement process is already an accessory. AppKit may return
    # False for this no-op; judge the resulting policy instead of that return.
    if app.activationPolicy() != policy:
        app.setActivationPolicy_(policy)
    if app.activationPolicy() != policy:
        raise RuntimeError("无法设置 macOS 应用的前后台运行方式")
