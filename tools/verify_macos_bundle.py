"""Validate architecture, deployment target, and linkage of a macOS app bundle."""

from __future__ import annotations

import argparse
from pathlib import Path
import plistlib
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from proximic_ring.platforms.macos.macho import inspect_macho, version_tuple


SYSTEM_PREFIXES = ("/System/Library/", "/usr/lib/")
PORTABLE_PREFIXES = ("@executable_path/", "@loader_path/", "@rpath/")


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
    allowed_version = version_tuple(minimum_macos)
    seen = set()
    count = 0
    for binary in app.rglob('*'):
        if not binary.is_file() or binary.resolve() in seen:
            continue
        seen.add(binary.resolve())
        relative = binary.relative_to(app)
        if binary.name == 'Info.plist':
            with binary.open('rb') as stream:
                metadata = plistlib.load(stream)
            minimum = metadata.get('LSMinimumSystemVersion')
            if minimum and version_tuple(minimum) > allowed_version:
                errors.append(f'bundle requires macOS {minimum}, above declared {minimum_macos}: {relative}')
        try:
            slices = inspect_macho(binary)
        except (ValueError, UnicodeError) as exc:
            errors.append(f'invalid Mach-O {relative}: {exc}')
            continue
        if not slices:
            continue
        count += 1
        arm = [s for s in slices if s['architecture'] == 'arm64']
        if not arm:
            errors.append(f'missing arm64 architecture: {relative}')
        for record in arm:
            actual = record['minimum_macos']
            if actual is None or version_tuple(actual) > allowed_version:
                errors.append(f'requires macOS {actual or "unknown"}, above declared {minimum_macos}: {relative}')
            for dependency in record['dependencies']:
                if not dependency.startswith(SYSTEM_PREFIXES + PORTABLE_PREFIXES):
                    errors.append(f'non-portable dependency {dependency}: {relative}')
            # Wheels can retain unused compiler search paths in LC_RPATH.
            # Those are not dependencies; check the actual load commands above.
    if not count:
        errors.append(f'no Mach-O files found in {app}')

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
