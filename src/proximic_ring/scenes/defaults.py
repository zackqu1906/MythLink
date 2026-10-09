"""Default mappings built from the common scene registry and action resolver."""
from ..app_gestures import APP_LABELS, default_profiles, profile_for_application
from .models import PRESENTATION
from .registry import SCENES, PENDING_TEMPLATES, PRESENTATION_GESTURES
from .capabilities import BROWSERS, application_scene_profiles, presentation_profile
from .adapters.presentation import scene_actions
from .adapters.browser import NAVIGATION, shared_navigation
from .resolver import resolve_action
from .menus import start_action

DEFAULTS_VERSION = 2

AUTO_ADD_PROFILES = frozenset({"codex", "workbuddy", "wps", "powerpoint"})

DEFAULT_MAPPINGS = {
    "codex": {"regular": {"circle-clockwise": "codex:next", "circle-counterclockwise": "codex:previous"}},
    "workbuddy": {"regular": {"circle-clockwise": "workbuddy:next", "circle-counterclockwise": "workbuddy:previous"}},
}

CHAT_ACTIONS = {"next": "下一个聊天", "previous": "上一个聊天"}


SCENE_DEFAULTS_VERSION = 2


def _scope_defaults(bundle, scene, profile, menus, *, entry=False):
    definition = SCENES[scene]
    template = definition.entry_gestures if entry else definition.gestures
    resolved, pending = {}, {}
    for gesture, choices in template.items():
        if not entry and bundle.casefold() in BROWSERS and gesture in NAVIGATION:
            continue
        for action in choices:
            item = resolve_action(bundle, scene, action, profile, menus, entry=entry)
            if item:
                resolved[gesture] = item
                break
        else:
            if definition.pending_format == "per_gesture":
                pending[gesture] = choices[0]
    return resolved, pending


def scene_defaults(bundle, profiles, menus=()):
    """Resolve defaults for any registered scene, including presentation."""
    resolved, pending = {}, {}
    for scene, profile in profiles.items():
        if scene not in SCENES:
            continue
        resolved[scene], waiting = _scope_defaults(bundle, scene, profile, menus)
        if waiting:
            pending[scene] = waiting
    return resolved, pending


def entry_defaults(bundle, scene, profile="", menus=()):
    """Scene-entry commands execute in the ordinary application scope."""
    return _scope_defaults(bundle, scene, profile, menus, entry=True)[0]


def pending_scene_defaults(bundle, profiles, menus=()):
    """Compatibility projection for existing per-gesture pending JSON fields.

    Presentation uses pendingStart; changing that format belongs to a separate
    migration, not this behavior-preserving module refactor.
    """
    return scene_defaults(bundle, {scene: profile for scene, profile in profiles.items()
                                  if scene in PENDING_TEMPLATES}, menus)


def default_profile(bundle: str, label: str = "", *, presentation: str = "") -> str:
    presenter = presentation_profile(bundle) or presentation
    if presenter:
        return presenter
    if bundle.casefold() == "com.tencent.workbuddy.mac":
        return "workbuddy"
    profile = profile_for_application(bundle, label)
    return profile if profile in DEFAULT_MAPPINGS else ""


def chat_actions(bundle: str, label: str, scene: str) -> list[dict]:
    profile = default_profile(bundle, label)
    if scene != "regular" or profile not in {"codex", "workbuddy"}:
        return []
    # Reuse the shortcuts already supported by the legacy app adapter.
    shortcuts = default_profiles()[profile]
    return [dict(id=f"{profile}:{key}", label=title, shortcut=shortcuts[key].shortcut,
                 path=APP_LABELS[profile] + " 默认快捷键", available=None, preset=True)
            for key, title in CHAT_ACTIONS.items()]


def default_mappings(bundle: str, label: str = "", *, presentation: str = "", scene_profiles=None, menus=()) -> dict[str, dict]:
    profiles = application_scene_profiles(bundle) if scene_profiles is None else dict(scene_profiles)
    result = _base_mappings(bundle, label, presentation=presentation, menus=menus)
    if PRESENTATION not in profiles and not presentation:
        result.pop(PRESENTATION, None)
        if presentation_profile(bundle):
            result.pop('regular', None)
    navigation = shared_navigation(bundle)
    if navigation:
        result.setdefault("regular", {}).update(navigation)
    result.update(pending_scene_defaults(bundle, profiles, menus)[0])
    return result


def _base_mappings(bundle: str, label: str = "", *, presentation: str = "", menus=()) -> dict[str, dict]:
    """Fresh, serializable records; callers cannot mutate the shared template."""
    profile = default_profile(bundle, label, presentation=presentation)
    presenter = presentation_profile(bundle) or presentation
    result = {}
    if presenter:
        return {"regular": entry_defaults(bundle, PRESENTATION, presenter, menus),
                **scene_defaults(bundle, {PRESENTATION: presenter}, menus)[0]}
    for scene, gestures in DEFAULT_MAPPINGS.get(profile, {}).items():
        actions = {item["id"]: item for item in
                   scene_actions(bundle, scene) + chat_actions(bundle, label, scene)}
        result[scene] = {gesture: {key: actions[action][key] for key in ("id", "label", "path", "shortcut")}
                         for gesture, action in gestures.items()}
    return result
