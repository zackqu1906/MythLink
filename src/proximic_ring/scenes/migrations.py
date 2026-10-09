"""Existing scene configuration migrations; no Qt or settings I/O."""
from .models import PRESENTATION, PDF, IMAGE, VIDEO, MUSIC
from .registry import SCENE_LABELS
from .capabilities import BROWSERS, MUSIC_APPS
from .policy import enabled_profiles, scene_choices
from .resolver import resolve_action
from .menus import menu_action
from .adapters.browser import NAVIGATION

LEGACY_MUSIC_VOLUME = {"circle-clockwise": "volume-up", "circle-counterclockwise": "volume-down"}

def migrate_enabled_scenes(bundle, profiles, data):
    """Old browsers keep all enabled scopes; native apps retain their old scope.

    Existing inactive bindings remain stored and can be re-enabled explicitly.
    Known music apps gain their correct audio default without deleting an older
    (previously misclassified) video scope. An explicit empty list stays empty.
    """
    if 'enabledScenes' in data:
        enabled = data['enabledScenes']
        if not isinstance(enabled, list) or any(scene not in SCENE_LABELS for scene in enabled):
            raise ValueError('Invalid enabled scene list')
        return [scene for scene in SCENE_LABELS if scene in profiles and scene in enabled]
    if bundle.casefold() in BROWSERS:
        return scene_choices(profiles)
    old = data.get('primaryScene', '')
    enabled = list(enabled_profiles(bundle, profiles, old))
    if bundle.casefold() in MUSIC_APPS and MUSIC in profiles and MUSIC not in enabled:
        enabled.append(MUSIC)
    return [scene for scene in SCENE_LABELS if scene in enabled]

def migrate_music_volume_defaults(bundle, app):
    """Upgrade old factory volume gestures once, preserving edits and clears.

    Resolved menu defaults retain their actual shortcut when moved. Custom
    circle bindings and occupied swipe bindings are never overwritten. Browsers
    previously omitted volume defaults because circles belong to tab navigation.
    Disabled scenes are migrated too, without enabling them.
    """
    if app.get("sceneDefaultsVersion", 0) >= 2:
        return False
    app["sceneDefaultsVersion"] = 2
    if app.get("removed") or app.get("defaultsCleared"):
        return True
    bindings = app.get("scenes", {}).get(MUSIC, {})
    pending = app.get("pendingDefaults", {}).get(MUSIC, {})
    if not bindings and not pending:
        return True  # Explicitly empty scenes stay empty.
    browser = bundle.casefold() in BROWSERS
    profile = app.get("sceneProfiles", {}).get(MUSIC, "")
    for old, meaning in LEGACY_MUSIC_VOLUME.items():
        gesture = "swipe-up" if meaning == "volume-up" else "swipe-down"
        previous = bindings.get(old)
        factory = resolve_action(bundle, MUSIC, meaning, profile)
        migrated = previous is not None and (previous == factory or (
            not previous["id"].startswith("custom:") and menu_action(MUSIC, meaning, [previous]) == previous))
        waiting = pending.get(old) == meaning
        if migrated:
            bindings.pop(old)
        if waiting:
            pending.pop(old)
        if gesture in bindings or gesture in pending or not (migrated or waiting or browser):
            continue
        record = previous if migrated else factory
        if record:
            bindings[gesture] = dict(record)
        else:
            pending[gesture] = meaning
    app.setdefault("scenes", {})[MUSIC] = bindings
    if pending:
        app.setdefault("pendingDefaults", {})[MUSIC] = pending
    else:
        app.get("pendingDefaults", {}).pop(MUSIC, None)
    return True


def migrate_presentation_presets(profile, bindings):
    """Repair only old built-in WPS keys; retain gesture positions and custom keys."""
    changed = False
    if profile == "wps":
        for item in bindings.values():
            replacements = {"wps:start-current": ("Cmd+Return", "Shift+F5"),
                            "wps:start-first": ("F5", "Cmd+Shift+Return")}
            previous, corrected = replacements.get(item["id"], (None, None))
            if item["shortcut"] == previous:
                item["shortcut"] = corrected
                changed = True
    return changed


def retire_browser_scene_navigation(bundle, app, scenes):
    """Retire old per-scene circle bindings; the caller owns backup persistence."""
    if bundle.casefold() in BROWSERS:
        for items in scenes.values():
            for gesture in NAVIGATION:
                items.pop(gesture, None)
        for items in app.get("pendingDefaults", {}).values():
            for gesture in NAVIGATION:
                items.pop(gesture, None)
        app["pendingDefaults"] = {scene: items for scene, items in app.get("pendingDefaults", {}).items() if items}
