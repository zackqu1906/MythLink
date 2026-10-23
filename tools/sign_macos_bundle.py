"""Sign all native code inside out and check the identities used by macOS TCC."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import plistlib
import re
import subprocess


IDENTIFIERS = {
    ".": "com.proximic.voice",
    "Contents/Helpers/ProxiMicInput.app": "com.proximic.inputmethod.ProxiMicVoice",
    "Contents/Helpers/ProxiMicPresence.app": "com.proximic.Presence",
    "Contents/Helpers/InputMethodAdmin": "com.proximic.InputMethodAdmin",
    "Contents/Helpers/MythlinkBrowser.app": "com.proximic.voice.browserhost",
    "Contents/Helpers/MythlinkBrowser.app/Contents/PlugIns/MythlinkBrowserExtension.appex": "com.proximic.voice.browserhost.extension",
}
MACHO_MAGIC = {
    b"\xfe\xed\xfa\xce", b"\xce\xfa\xed\xfe", b"\xfe\xed\xfa\xcf", b"\xcf\xfa\xed\xfe",
    b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca", b"\xca\xfe\xba\xbf", b"\xbf\xba\xfe\xca",
}


def run(*args: str | Path) -> str:
    result = subprocess.run([str(x) for x in args], text=True, capture_output=True)
    if result.returncode:
        raise RuntimeError(f"{args[0]} failed for {args[-1]}:\n{result.stdout}{result.stderr}")
    return result.stdout + result.stderr


def signing_targets(app: Path) -> list[Path]:
    """Include extension modules in data directories, but never follow symlinks."""
    targets = {app}
    for root, dirs, files in os.walk(app, followlinks=False):
        for name in dirs:
            path = Path(root) / name
            if not path.is_symlink() and path.suffix in {".app", ".framework", ".xpc", ".appex"}:
                targets.add(path)
        for name in files:
            path = Path(root) / name
            if path.is_symlink():
                continue
            with path.open("rb") as stream:
                if stream.read(4) in MACHO_MAGIC:
                    targets.add(path)
    return sorted(targets, key=lambda path: (-len(path.parts), str(path)))


def check_identity(details: str, requirement: str, identifier: str, team: str) -> None:
    if f"Identifier={identifier}" not in details.splitlines():
        raise ValueError(f"Unexpected code identifier; expected {identifier}")
    if f"TeamIdentifier={team}" not in details.splitlines():
        raise ValueError(f"Unexpected signing team for {identifier}; expected {team}")
    if "Authority=Developer ID Application:" not in details:
        raise ValueError(f"Not Developer ID Application signed: {identifier}")
    if "(runtime)" not in details or "Timestamp=" not in details:
        raise ValueError(f"Missing hardened runtime or secure timestamp: {identifier}")
    if "cdhash" in requirement or "anchor apple generic" not in requirement:
        raise ValueError(f"Unstable or non-Apple designated requirement: {identifier}")
    if team not in requirement or f'identifier "{identifier}"' not in requirement:
        raise ValueError(f"Designated requirement does not bind the expected identity: {identifier}")


def check_bundle_identifiers(app: Path) -> None:
    for relative, identifier in IDENTIFIERS.items():
        path = app / relative
        if not path.exists():
            raise ValueError(f"Missing signed component: {relative}")
        if path.suffix in {".app", ".appex"}:
            with (path / "Contents/Info.plist").open("rb") as stream:
                info = plistlib.load(stream)
            if info.get("CFBundleIdentifier") != identifier:
                raise ValueError(f"Bundle identifier changed: {relative}; expected {identifier}")


def webengine_entitlements(path: Path) -> Path | None:
    """Preserve Qt's supplied Chromium helper entitlements when re-signing."""
    if path.name not in {'QtWebEngineProcess', 'QtWebEngineProcess.app'}:
        return None
    helper = next((p for p in (path, *path.parents) if p.name == 'QtWebEngineProcess.app'), None)
    if helper is None: return None
    entitlements = helper / 'Contents/Resources/QtWebEngineProcess.entitlements'
    if not entitlements.is_file():
        raise ValueError(f'Missing Qt WebEngine helper entitlements: {entitlements}')
    return entitlements


def sign_bundle(app: Path, identity: str, team: str, entitlements: Path, report: Path,
                keychain: str | None = None) -> None:
    previous = json.loads(report.read_text()) if report.exists() else None
    check_bundle_identifiers(app)
    targets = signing_targets(app)
    flags = ["--force", "--sign", identity]
    if keychain:
        flags += ["--keychain", keychain]
    if identity != "-":
        flags += ["--options", "runtime", "--timestamp"]
    for index, path in enumerate(targets, 1):
        relative = str(path.relative_to(app))
        extra = []
        if relative in IDENTIFIERS:
            extra += ["--identifier", IDENTIFIERS[relative]]
        if path == app:
            extra += ["--entitlements", str(entitlements)]
        elif path.suffix == ".appex" and path.name == "MythlinkBrowserExtension.appex":
            extra += ["--entitlements", str(path / "Contents/Resources/extension.entitlements.plist")]
        elif helper_entitlements := webengine_entitlements(path):
            extra += ["--entitlements", str(helper_entitlements)]
        run("/usr/bin/codesign", *flags, *extra, path)
        if index % 50 == 0 or path == app:
            print(f"Signed {index}/{len(targets)} code items", flush=True)
    run("/usr/bin/codesign", "--verify", "--deep", "--strict", app)
    # Verify every item too, including modules outside standard code locations.
    for path in targets:
        run("/usr/bin/codesign", "--verify", "--strict", path)
        if identity != "-":
            details = run("/usr/bin/codesign", "-d", "--verbose=4", path)
            if f"TeamIdentifier={team}" not in details.splitlines():
                raise ValueError(f"Nested code has a different signing team: {path}")
    components = {}
    for relative, identifier in IDENTIFIERS.items():
        path = app / relative
        details = run("/usr/bin/codesign", "-d", "--verbose=4", path)
        output = run("/usr/bin/codesign", "-d", "-r-", path)
        match = re.search(r"^designated => (.+)$", output, re.MULTILINE)
        if not match and identity != "-":
            raise ValueError(f"Missing designated requirement: {relative}")
        # Ad-hoc development signatures may have no designated requirement.
        # They are never accepted as a stable update/distribution identity.
        requirement = match.group(1) if match else None
        if identity != "-":
            check_identity(details, requirement, identifier, team)
            if previous and previous.get("team") == team and relative in previous.get("components", {}):
                old = previous["components"][relative]["requirement"]
                run("/usr/bin/codesign", "--verify", "--strict", "-R", f"={old}", path)
        components[relative] = {"identifier": identifier, "requirement": requirement}
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps({"team": team if identity != "-" else None,
                                  "components": components}, indent=2) + "\n")
    print(f"Signature and update identity checks passed: {report}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("app", type=Path)
    parser.add_argument("--identity", required=True)
    parser.add_argument("--team", required=True)
    parser.add_argument("--entitlements", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    sign_bundle(args.app.resolve(), args.identity, args.team, args.entitlements, args.report,
                os.environ.get("APPLE_SIGNING_KEYCHAIN"))


if __name__ == "__main__":
    main()
