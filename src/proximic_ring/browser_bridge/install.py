"""Prepare immutable extension resources and register Chrome's native host."""
from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
import re
import plistlib
import shlex
import shutil
import sys
import subprocess

from .protocol import HOST_NAME

RESOURCES = Path(__file__).parent / "extension"


def chrome_id():
    configured = json.loads((Path(__file__).parent / "release.json").read_text()).get("chrome_store_id", "")
    if configured:
        if not re.fullmatch(r"[a-p]{32}", configured):
            raise ValueError("invalid Chrome Web Store extension ID")
        return configured
    manifest = json.loads((RESOURCES / "manifest.chrome.json").read_text())
    digest = hashlib.sha256(base64.b64decode(manifest["key"])).hexdigest()[:32]
    return "".join(chr(ord("a") + int(char, 16)) for char in digest)


def store_url():
    configured = json.loads((Path(__file__).parent / "release.json").read_text()).get("chrome_store_id", "")
    return "https://chromewebstore.google.com/detail/" + chrome_id() if configured else ""


def chrome_extension_state(home=None):
    """Read only our ID from standard Chrome profiles, never infer from heartbeat.

    Preferences are a best-effort local installation hint, not a permission or
    connection API. New Chrome versions may omit `state`; keep that as installed
    rather than guessing enabled. Never modify browser preferences.
    """
    home = Path(home) if home else Path.home()
    root = home / "Library/Application Support/Google/Chrome"
    if not root.exists():
        return "not_installed"
    try:
        profiles = [p for p in root.iterdir() if p.is_dir() and
                    (p.name == "Default" or re.fullmatch(r"Profile \d+", p.name))]
    except OSError:
        return "unknown"
    found, uncertain = set(), False
    for profile in profiles:
        entry = None
        for name in ("Preferences", "Secure Preferences"):
            path = profile / name
            try:
                data = json.loads(path.read_text())
                candidate = data.get("extensions", {}).get("settings", {}).get(chrome_id())
                if isinstance(candidate, dict):
                    entry = candidate
            except FileNotFoundError:
                continue
            except (OSError, ValueError, TypeError, AttributeError):
                uncertain = True
        if entry is None or entry.get("state") == 2:  # external-uninstalled tombstone
            continue
        if not entry.get("path") and not isinstance(entry.get("manifest"), dict):
            uncertain = True
            continue
        if entry.get("state") == 0 or entry.get("disable_reasons"):
            found.add("disabled")
        else:
            found.add("enabled" if entry.get("state") == 1 else "installed")
    for state in ("enabled", "installed", "disabled"):
        if state in found:
            return state
    return "unknown" if uncertain else "not_installed"


def open_safari_settings(path=None):
    """Run the containing app's explicit command, even if its UI is already open."""
    path = Path(path) if path is not None else safari_helper()
    if not safari_helper_available(path):
        raise FileNotFoundError(path)
    with (path / "Contents/Info.plist").open("rb") as stream:
        info = plistlib.load(stream)
    if info.get("MythlinkSupportsDirectSettings") is True:
        subprocess.run([str(path / "Contents/MacOS/MythlinkBrowser"), "--open-settings"],
                       check=True, capture_output=True, text=True, timeout=8)
    else:
        # Backward compatibility for an older packaged helper.
        subprocess.run(["/usr/bin/open", str(path), "--args", "--open-settings"],
                       check=True, capture_output=True, timeout=5)


def export_extension(destination, browser, *, store=False):
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    for source in RESOURCES.iterdir():
        if source.is_file() and not source.name.startswith("manifest."):
            shutil.copy2(source, destination / source.name)
    manifest = json.loads((RESOURCES / f"manifest.{browser}.json").read_text())
    if store:
        manifest.pop("key", None)
    (destination / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    (destination / "config.js").write_text(f'globalThis.MYTHLINK_BROWSER = "{browser}";\n')
    return destination


def install_chrome(home=None):
    home = Path(home) if home else Path.home()
    support = home / "Library/Application Support/Mythlink/BrowserExtension"
    support.mkdir(parents=True, exist_ok=True)
    launcher = support / "native-host"
    if getattr(sys, "frozen", False):
        command = [sys.executable, "--browser-native-host"]
        prefix = ""
    else:
        command = [sys.executable, "-m", "proximic_ring.browser_bridge.native_host"]
        prefix = "export PYTHONPATH=" + shlex.quote(str(Path(__file__).resolve().parents[2])) + "\n"
    launcher.write_text("#!/bin/sh\n" + prefix + "exec " + shlex.join(command) + "\n")
    launcher.chmod(0o700)
    manifest = {"name": HOST_NAME, "description": "Mythlink browser media bridge", "path": str(launcher),
                "type": "stdio", "allowed_origins": [f"chrome-extension://{chrome_id()}/"]}
    hosts = home / "Library/Application Support/Google/Chrome/NativeMessagingHosts"
    hosts.mkdir(parents=True, exist_ok=True)
    target = hosts / (HOST_NAME + ".json")
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    temporary.chmod(0o600)
    temporary.replace(target)
    return export_extension(support / "Chrome", "chrome")


def safari_helper():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parents[1] / "Helpers/MythlinkBrowser.app"
    return Path(__file__).resolve().parents[3] / ".build/browser-extension/MythlinkBrowser.app"


def safari_helper_available(path=None):
    path = Path(path) if path is not None else safari_helper()
    return all((path / relative).is_file() for relative in (
        "Contents/MacOS/MythlinkBrowser",
        "Contents/PlugIns/MythlinkBrowserExtension.appex/Contents/MacOS/MythlinkBrowserExtension",
        "Contents/PlugIns/MythlinkBrowserExtension.appex/Contents/Resources/manifest.json"))


def safari_extension_state():
    """Read Safari's public extension state, not connection heartbeat or prefs."""
    path = safari_helper()
    if not safari_helper_available(path):
        return "component_missing"
    try:
        with (path / "Contents/Info.plist").open("rb") as stream:
            info = plistlib.load(stream)
        # A helper without LSUIElement enters the Dock before its status-mode
        # code can hide it. Source launches may still have an older compiled
        # helper; do not repeatedly launch it until the helper is rebuilt.
        # Helpers without the status switch would open a window as well.
        if (info.get("MythlinkSupportsExtensionStatus") is not True
                or info.get("LSUIElement") is not True):
            return "unknown"
        result = subprocess.run([str(path / "Contents/MacOS/MythlinkBrowser"), "--extension-status"],
                                check=True, capture_output=True, text=True, timeout=3)
        state = json.loads(result.stdout).get("state")
        return state if state in {"enabled", "disabled", "unavailable"} else "unknown"
    except (OSError, ValueError, TypeError, AttributeError, subprocess.SubprocessError):
        return "unknown"
