"""Validate architecture, deployment target, and linkage of a macOS app bundle."""

from __future__ import annotations

import argparse
from pathlib import Path
import plistlib
import re
import subprocess


SYSTEM_PREFIXES = ("/System/Library/", "/usr/lib/")
PORTABLE_PREFIXES = ("@executable_path/", "@loader_path/", "@rpath/")


def _run(*args: str | Path) -> str:
    return subprocess.run(
        [str(item) for item in args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def _version(value: str) -> tuple[int, ...]:
    parts = tuple(int(part) for part in value.split("."))
    return parts + (0,) * (3 - len(parts))


def verify_bundle(app: Path, minimum_macos: str) -> list[str]:
    errors: list[str] = []
    with (app / "Contents" / "Info.plist").open("rb") as stream:
        info = plistlib.load(stream)
    if info.get("LSUIElement") is not True or info.get("LSBackgroundOnly"):
        errors.append("bundle must start as LSUIElement; only the main UI may enable its Dock icon")
    browser_info = app / "Contents/Helpers/MythlinkBrowser.app/Contents/Info.plist"
    if browser_info.is_file():
        with browser_info.open("rb") as stream:
            browser = plistlib.load(stream)
        if browser.get("LSUIElement") is not True or browser.get("LSBackgroundOnly"):
            errors.append("MythlinkBrowser must start as LSUIElement; periodic status checks must not enter the Dock")
    resources_root = app / "Contents" / "Resources"
    files = [item for item in app.rglob("*") if not item.is_symlink() and item.is_file()]
    allowed_version = _version(minimum_macos)
    for path in files:
        if path.name != "Info.plist":
            continue
        with path.open("rb") as stream:
            bundle_info = plistlib.load(stream)
        declarations = [bundle_info.get("LSMinimumSystemVersion")]
        declarations.append(bundle_info.get("LSMinimumSystemVersionByArchitecture", {}).get("arm64"))
        for actual in declarations:
            if actual and _version(actual) > allowed_version:
                errors.append(
                    f"declares macOS {actual}, above target {minimum_macos}: {path.relative_to(app)}"
                )
    mach_o_files = [
        item for item in files if "Mach-O" in _run("/usr/bin/file", item)
    ]
    if not mach_o_files:
        return errors + [f"no Mach-O files found in {app}"]

    for binary in mach_o_files:
        relative = binary.relative_to(app)
        description = _run("/usr/bin/file", binary)
        if "arm64" not in description:
            errors.append(f"Mach-O binary is not arm64: {relative}")

        load_commands = _run("/usr/bin/otool", "-l", binary)
        minimum_versions = re.findall(r"\bminos\s+(\d+(?:\.\d+)*)", load_commands)
        minimum_versions += re.findall(
            r"\bLC_VERSION_MIN_MACOSX\s+cmdsize\s+\d+\s+version\s+(\d+(?:\.\d+)*)",
            load_commands,
        )
        for actual in minimum_versions:
            if _version(actual) > allowed_version:
                errors.append(
                    f"requires macOS {actual}, above declared {minimum_macos}: {relative}"
                )

        linked = _run("/usr/bin/otool", "-L", binary).splitlines()[1:]
        for line in linked:
            dependency = line.strip().split(" (", 1)[0]
            if dependency.startswith(SYSTEM_PREFIXES + PORTABLE_PREFIXES):
                continue
            if dependency.startswith(str(app)):
                continue
            errors.append(f"non-portable dependency {dependency}: {relative}")

    if not resources_root.is_dir():
        errors.append("missing Contents/Resources")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("app", type=Path)
    parser.add_argument("--minimum-macos", required=True)
    args = parser.parse_args()
    errors = verify_bundle(args.app.resolve(), args.minimum_macos)
    if errors:
        print("macOS bundle verification failed:")
        for error in errors:
            print(f"- {error}")
        return 1
    print(f"macOS bundle portability check passed: {args.app}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
