"""Build Chrome unpacked/submission packages and a macOS Safari containing app.

Uses the installed macOS SDK; Xcode's UI/project generator is not required.
Signing/notarization of the complete Mythlink app remains in build-macos.sh.
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from proximic_ring.browser_bridge.install import export_extension, chrome_id


def plist(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as stream:
        plistlib.dump(data, stream)


def build(destination, identity="-", *, safari=True):
    destination = destination.resolve()
    destination.mkdir(parents=True, exist_ok=True)
    export_extension(destination / "Chrome", "chrome")
    store = export_extension(destination / "Chrome-Store", "chrome", store=True)
    with zipfile.ZipFile(destination / "Mythlink-Chrome-0.1.0.zip", "w", zipfile.ZIP_DEFLATED) as archive:
        for file in sorted(store.iterdir()):
            archive.write(file, file.name)
    shutil.copy2(ROOT / "docs/BROWSER_EXTENSION.md", destination / "安装与发布说明.md")
    report = {"chrome_development_id": chrome_id(), "chrome_submission": "Mythlink-Chrome-0.1.0.zip",
              "safari_signed_release": identity != "-", "version": "0.1.0"}
    if safari:
        source = ROOT / "native/MythlinkBrowser"
        app = destination / "MythlinkBrowser.app"
        appex = app / "Contents/PlugIns/MythlinkBrowserExtension.appex"
        for bundle in (app, appex):
            (bundle / "Contents/MacOS").mkdir(parents=True, exist_ok=True)
        common = dict(CFBundleDevelopmentRegion="zh_CN", CFBundleName="Mythlink 浏览器助手",
                      CFBundleDisplayName="Mythlink 浏览器助手", CFBundleVersion="1",
                      CFBundleShortVersionString="0.1.0", LSMinimumSystemVersion="15.0")
        plist(app / "Contents/Info.plist", {**common, "CFBundleIdentifier": "com.proximic.voice.browserhost",
            "CFBundleExecutable": "MythlinkBrowser", "CFBundlePackageType": "APPL", "NSPrincipalClass": "NSApplication",
            # Status polling starts this bundle every few seconds. AppKit
            # registers it before main.swift can change the activation policy.
            # The explicit UI entry promotes itself to .regular when needed.
            "LSUIElement": True,
            "MythlinkSupportsExtensionStatus": True, "MythlinkSupportsDirectSettings": True})
        plist(appex / "Contents/Info.plist", {**common, "CFBundleIdentifier": "com.proximic.voice.browserhost.extension",
            "CFBundleExecutable": "MythlinkBrowserExtension", "CFBundlePackageType": "XPC!",
            "NSAppTransportSecurity": {"NSAllowsLocalNetworking": True},
            "NSExtension": {"NSExtensionPointIdentifier": "com.apple.Safari.web-extension",
                            "NSExtensionPrincipalClass": "SafariWebExtensionHandler"}})
        export_extension(appex / "Contents/Resources", "safari")
        sdk = subprocess.check_output(["xcrun", "--sdk", "macosx", "--show-sdk-path"], text=True).strip()
        cache = ROOT / ".build/swift-browser-cache"
        cache.mkdir(parents=True, exist_ok=True)
        flags = ["-sdk", sdk, "-target", "arm64-apple-macosx15.0", "-O", "-module-cache-path", str(cache), "-framework", "SafariServices"]
        subprocess.run(["xcrun", "swiftc", *flags, str(source / "main.swift"), "-o", str(app / "Contents/MacOS/MythlinkBrowser")], check=True)
        subprocess.run(["xcrun", "swiftc", *flags, "-parse-as-library", "-application-extension", "-emit-executable",
            "-Xlinker", "-e", "-Xlinker", "_NSExtensionMain", str(source / "SafariWebExtensionHandler.swift"),
            "-o", str(appex / "Contents/MacOS/MythlinkBrowserExtension")], check=True)
        # Save the required entitlement file so the enclosing app's final
        # inside-out re-sign retains the extension's sandbox and network access.
        shutil.copy2(source / "extension.entitlements.plist", appex / "Contents/Resources/extension.entitlements.plist")
        flags = ["--force", "--sign", identity]
        if os.environ.get("APPLE_SIGNING_KEYCHAIN"):
            flags += ["--keychain", os.environ["APPLE_SIGNING_KEYCHAIN"]]
        if identity != "-":
            flags += ["--options", "runtime", "--timestamp"]
        subprocess.run(["codesign", *flags, "--entitlements", str(source / "extension.entitlements.plist"), str(appex)], check=True)
        subprocess.run(["codesign", *flags, str(app)], check=True)
        subprocess.run(["codesign", "--verify", "--deep", "--strict", str(app)], check=True)
    (destination / "build-report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(destination)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / ".build/browser-extension")
    parser.add_argument("--identity", default="-")
    parser.add_argument("--chrome-only", action="store_true")
    options = parser.parse_args()
    build(options.output, options.identity, safari=not options.chrome_only)
