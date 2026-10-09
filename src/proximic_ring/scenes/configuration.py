"""Scene configuration lifecycle, independent of the Qt editor and dispatcher.

The controller owns signals, settings I/O and generation invalidation. These
functions preserve the existing JSON schema, default versions and user edits.
"""
from .models import PRESENTATION
from .registry import PENDING_TEMPLATES as TEMPLATES
from .capabilities import presentation_profile, installed_scene_profiles
from .adapters.presentation import PROFILES
from .policy import primary_scene, enabled_profiles
from .defaults import (default_mappings, DEFAULTS_VERSION, SCENE_DEFAULTS_VERSION,
                       pending_scene_defaults as scene_defaults)
from .resolver import resolve_action

SETTINGS_KEY = "gestures/applicationMenusV1"

def defaults_for(bundle, app, menus=()):
    profiles = enabled_profiles(bundle, app.get("sceneProfiles", {}), app.get("primaryScene", ""), app.get("enabledScenes"))
    return default_mappings(bundle, app.get("label", ""),
                            presentation=profiles.get(PRESENTATION, ""),
                            scene_profiles=profiles, menus=menus)

def upgrade_defaults(apps, defaults_for):
    changed = False
    # One-time fill for existing populated records. Custom keys win, and
    # explicitly empty scopes remain empty. Future clears never refill.
    for bundle, app in apps.items():
        if app.get("removed") or app.get("defaultsVersion", 0) >= DEFAULTS_VERSION:
            continue
        defaults = {scene: items for scene, items in defaults_for(bundle, app).items()
                    if scene in {"regular", PRESENTATION}}
        if not defaults:
            continue
        if app.get("bindings") or any(app.get("scenes", {}).values()):
            for scene, items in defaults.items():
                existing = app.get("bindings") if scene == "regular" else app.get("scenes", {}).get(scene)
                if existing == {}:
                    continue
                if scene == "regular":
                    target = app["bindings"]
                else:
                    target = app.setdefault("scenes", {}).setdefault(scene, {})
                for gesture, action in items.items():
                    target.setdefault(gesture, action)
        app["defaultsVersion"] = DEFAULTS_VERSION
        changed = True
    return changed


def initialize_scene_defaults(apps, profiles_for):
    changed = False
    # A scope is initialized once, even if its shortcuts have not been read.
    # Existing scopes (including empty ones) are owned by the user.
    for bundle, app in apps.items():
        if app.get("removed"):
            continue
        profiles = profiles_for(bundle)
        modes = [scene for scene in profiles if scene in TEMPLATES]
        if not modes:
            continue
        initialized = set(app.get("initializedScenes", []))
        resolved, pending = scene_defaults(bundle, profiles)
        for scene in modes:
            if scene in initialized:
                continue
            if not app.get("defaultsCleared") and scene not in app.get("scenes", {}):
                app.setdefault("scenes", {})[scene] = resolved[scene]
                if pending.get(scene):
                    app.setdefault("pendingDefaults", {})[scene] = pending[scene]
            initialized.add(scene)
            changed = True
        app["initializedScenes"] = [scene for scene in TEMPLATES if scene in initialized]
        if app.get("sceneDefaultsVersion", 0) != SCENE_DEFAULTS_VERSION:
            app["sceneDefaultsVersion"] = SCENE_DEFAULTS_VERSION
            changed = True
    return changed


def resolve_pending_defaults(bundle, app, profiles, menus):
    pending = {scene: dict(items) for scene, items in app.get("pendingDefaults", {}).items()}
    changed = False
    for scene, items in pending.items():
        if scene not in profiles:
            continue
        target = app.setdefault("scenes", {}).setdefault(scene, {})
        for gesture, action in list(items.items()):
            if gesture in target:
                del items[gesture]  # A saved user action always wins.
                changed = True
                continue
            record = resolve_action(bundle, scene, action, profiles.get(scene, ""), menus)
            if record:
                target[gesture] = record
                del items[gesture]
                changed = True
    if changed:
        app["pendingDefaults"] = {scene: items for scene, items in pending.items() if items}
    return changed


def refresh_pristine_defaults(bundle, app, profiles, menus):
    """Prefer the app's complete menu over unchanged built-in key adapters.

    Exact factory records only: recorded keys, edited/deleted actions and
    already resolved menu records remain user-owned.
    """
    changed = False
    for scene, profile in profiles.items():
        for gesture, meaning in TEMPLATES.get(scene, {}).items():
            target = app.get("scenes", {}).get(scene, {})
            original = resolve_action(bundle, scene, meaning, profile)
            if original and target.get(gesture) == original:
                discovered = resolve_action(bundle, scene, meaning, profile, menus)
                if discovered and discovered != original:
                    target[gesture] = discovered
                    changed = True
    return changed


def new_application(candidate):
    bundle = candidate["value"]
    capabilities = {**candidate.get("sceneProfiles", {}),
                    **installed_scene_profiles(bundle, candidate.get("path", ""))}
    profile = presentation_profile(bundle) or candidate.get("presentationProfile", "") or capabilities.get(PRESENTATION, "")
    app = dict(label=candidate["label"], path=candidate.get("path", ""),
               sceneProfiles=capabilities, presentationProfile=profile if profile in PROFILES else "")
    if app["presentationProfile"]:
        capabilities[PRESENTATION] = app["presentationProfile"]
    app["primaryScene"] = primary_scene(bundle, capabilities, candidate.get("primaryScene", ""))
    selected_profiles = enabled_profiles(bundle, capabilities, app["primaryScene"])
    app["enabledScenes"] = list(selected_profiles)
    defaults = defaults_for(bundle, app)
    _, pending = scene_defaults(bundle, selected_profiles)
    app.update(initializedScenes=[scene for scene in TEMPLATES if scene in selected_profiles],
               pendingDefaults=pending, sceneDefaultsVersion=SCENE_DEFAULTS_VERSION, defaultsCleared=False,
               bindings=defaults.get("regular", {}),
               scenes={scene: items for scene, items in defaults.items() if scene != "regular"},
               defaultsVersion=DEFAULTS_VERSION,
               pendingStart=bool(PRESENTATION in selected_profiles and "snap" not in defaults.get("regular", {})))
    return app
