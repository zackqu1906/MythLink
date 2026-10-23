"""Read-only application names/locations for history presentation."""
from functools import lru_cache
from pathlib import Path, PureWindowsPath
import re
import sys

from ...desktop.application_catalog import application_metadata


def _native_application(identity):
    if sys.platform != "darwin":
        return None
    import AppKit
    import objc
    with objc.autorelease_pool():
        workspace = AppKit.NSWorkspace.sharedWorkspace()
        path = identity if identity.startswith("/") and identity.endswith(".app") else ""
        if not path:
            url = workspace.URLForApplicationWithBundleIdentifier_(identity)
            path = str(url.path()) if url else ""
        if not path:
            path = str(workspace.fullPathForApplication_(identity) or "")
        return application_metadata(Path(path)) if path else None


@lru_cache(maxsize=256)
def _resolved_application(identity):
    if not identity:
        return "未记录应用", "", ""
    if identity == "com.proximic.voice":
        return "Mythlink", identity, ""
    try:
        app = _native_application(identity)
    except Exception:
        app = None
    if app:
        return app["label"], app["value"], app["path"]
    # Old records can contain a process name or executable path instead of a
    # bundle identifier. Preserve a friendly name, never show an unresolved ID.
    name = PureWindowsPath(identity).name if "\\" in identity else Path(identity).name
    if name.casefold().endswith((".exe", ".app")):
        name = name.rsplit(".", 1)[0]
    if re.fullmatch(r"[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)+", name):
        name = "未识别应用"
    return name or "未识别应用", "", ""


def application_presentation(identity):
    name, bundle, path = _resolved_application(str(identity or "").strip())
    return dict(name=name, bundle=bundle, path=path)
