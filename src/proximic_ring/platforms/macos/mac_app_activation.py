"""Give only the main macOS UI a Dock icon; workers must stay background-only."""
from __future__ import annotations

import sys


def require_gui_session() -> None:
    """Reject an unavailable desktop before AppKit aborts during registration."""
    if sys.platform != "darwin":
        return
    import ctypes

    system = ctypes.CDLL("/usr/lib/libSystem.B.dylib")
    port_type = ctypes.c_uint
    lookup = system.bootstrap_look_up
    lookup.argtypes = [port_type, ctypes.c_char_p, ctypes.POINTER(port_type)]
    lookup.restype = ctypes.c_int
    release = system.mach_port_deallocate
    release.argtypes = [port_type, port_type]
    release.restype = ctypes.c_int
    bootstrap = port_type.in_dll(system, "bootstrap_port").value
    task = port_type.in_dll(system, "mach_task_self_").value
    # Looking up these services is safe even when a sandbox denies access;
    # NSApplication.sharedApplication() would instead terminate with SIGABRT.
    for service in (b"com.apple.coreservices.launchservicesd", b"com.apple.windowserver.active"):
        port = port_type()
        status = lookup(bootstrap, service, ctypes.byref(port))
        if status != 0:
            raise RuntimeError("当前启动环境无法访问 macOS 图形会话，请从终端或应用图标启动 Mythlink。")
        release(task, port.value)


def configure_app_activation(*, background: bool) -> None:
    """Call on the process's main thread, before loading its UI or worker.

    The frozen bundle starts with LSUIElement, so even its bootloader does not
    add a temporary Dock icon. Only the normal Qt host promotes itself to a
    regular application. This does not activate it or request any permission.
    """
    if sys.platform != "darwin":
        return
    require_gui_session()
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
