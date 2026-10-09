"""Installed macOS application metadata for the explicit application picker."""
from __future__ import annotations

import os
from pathlib import Path
import plistlib
import subprocess

from .scenes.capabilities import presentation_profile
from .scenes.capabilities import application_scene_profiles


def application_roots():
    return (Path('/Applications'), Path('/System/Applications'), Path.home() / 'Applications',
            Path('/System/Library/CoreServices/Applications'))


def spotlight_application_paths():
    """Include indexed apps outside the standard folders, without launching them."""
    try:
        result = subprocess.run(['/usr/bin/mdfind', '-0',
                                 "kMDItemContentType == 'com.apple.application-bundle'"],
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=3, check=True)
        return [Path(os.fsdecode(path)) for path in result.stdout.split(b'\0') if path], False
    except (OSError, subprocess.SubprocessError):
        return [], True


def application_paths(roots, indexed):
    """Never descend into an app's Contents or enumerate embedded helper apps."""
    paths, errors = set(indexed), []
    for root in roots:
        if not root.is_dir():
            continue
        for directory, folders, _ in os.walk(root, followlinks=False, onerror=errors.append):
            descend = []
            for name in folders:
                path = Path(directory) / name
                if path.suffix.casefold() == '.app':
                    paths.add(path)
                elif not name.startswith('.') and path.suffix.casefold() not in {'.framework', '.bundle', '.appex'}:
                    descend.append(name)
            folders[:] = descend
    return sorted(paths, key=lambda path: (not str(path).startswith('/Applications/'), str(path))), bool(errors)


def application_metadata(path):
    path = Path(path)
    if (not path.is_absolute() or path.suffix.casefold() != '.app'
            or any(parent.suffix.casefold() in {'.app', '.framework', '.appex'} for parent in path.parents)):
        return None
    try:
        path = path.resolve(strict=True)
        with (path / 'Contents/Info.plist').open('rb') as stream:
            info = plistlib.load(stream)
        if not isinstance(info, dict):
            return None
        bundle = info.get('CFBundleIdentifier')
        if (not isinstance(bundle, str) or not bundle or info.get('LSBackgroundOnly')
                or info.get('CFBundlePackageType', 'APPL') != 'APPL'):
            return None
        label = str(info.get('CFBundleDisplayName') or info.get('CFBundleName') or path.stem)
        # Use Finder's localized display name when available (e.g. Terminal / 终端).
        try:
            from Foundation import NSFileManager
            import objc
            with objc.autorelease_pool():
                label = str(NSFileManager.defaultManager().displayNameAtPath_(str(path)))
            if label.casefold().endswith('.app'):
                label = label[:-4]
        except (ImportError, AttributeError):
            pass
        aliases = ' '.join(str(info.get(key) or '') for key in ('CFBundleDisplayName', 'CFBundleName', 'CFBundleExecutable'))
        from .scenes.policy import primary_scene
        profiles = application_scene_profiles(bundle, info)
        return dict(value=bundle, label=label, path=str(path), running=False,
                    search=f'{label} {path.stem} {aliases} {bundle}'.casefold(),
                    presentationProfile=presentation_profile(bundle, info),
                    sceneProfiles=profiles,
                    primaryScene=primary_scene(bundle, profiles, category=info.get('LSApplicationCategoryType', '')))
    except (OSError, ValueError, TypeError, plistlib.InvalidFileException):
        return None


def installed_applications(running, *, roots=None, indexed=None):
    """Merge installed apps with a fresh running snapshot, deduplicating by bundle."""
    partial = False
    if indexed is None:
        indexed, partial = spotlight_application_paths()
    paths, scan_errors = application_paths(application_roots() if roots is None else roots, indexed)
    apps = {}
    for path in paths:
        item = application_metadata(path)
        if item:
            apps.setdefault(item['value'], item)
    for app in running:
        bundle = app['value']
        previous = apps.get(bundle, {})
        label, path = app['label'], app.get('path') or previous.get('path', '')
        metadata = previous or (application_metadata(path) if path else None) or {}
        apps[bundle] = dict(value=bundle, label=label, path=path, running=True,
                            presentationProfile=metadata.get('presentationProfile', presentation_profile(bundle)),
                            sceneProfiles=metadata.get('sceneProfiles', application_scene_profiles(bundle)),
                            primaryScene=metadata.get('primaryScene', ''),
                            search=f"{previous.get('search', '')} {label} {bundle}".casefold())
    return dict(candidates=sorted(apps.values(), key=lambda app: (not app['running'], app['label'].casefold())),
                partial=partial or scan_errors)
