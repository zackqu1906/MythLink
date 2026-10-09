"""One action-resolution boundary; retain existing per-scene priorities."""
from ..app_gestures import normalize_shortcut
from .models import PRESENTATION, PDF, IMAGE, VIDEO, MUSIC
from .registry import SCENES, SCENE_LABELS, action_label
from .capabilities import BROWSERS
from .adapters.presentation import scene_actions
from .adapters.native import activity_actions, ADAPTERS
from .adapters.browser import video_actions
from .menus import menu_action, start_action


def _presentation_action(bundle, action, profile, menus, *, entry=False):
    scope = "regular" if entry else PRESENTATION
    actions = {item["id"].split(":")[-1]: item for item in scene_actions(bundle, scope, profile=profile)}
    if not entry:
        # Existing generic slideshow fallbacks, unchanged by this refactor.
        for key, title, shortcut in [("previous", "上一页", "Left"), ("next", "下一页", "Right"), ("end", "结束放映", "Escape")]:
            actions.setdefault(key, dict(id="presentation:" + key, label=title,
                                        path="放映通用快捷键", shortcut=shortcut))
    else:
        discovered = start_action(menus)
        if discovered and "start-current" not in actions and "start-first" not in actions:
            actions["start-current"] = discovered
    item = actions.get(action)
    return {key: item[key] for key in ("id", "label", "path", "shortcut")} if item else None


def resolve_action(bundle, scene, action, profile="", menus=(), *, entry=False):
    definition = SCENES.get(scene)
    if definition is not None and definition.resolution == "preset_first":
        return _presentation_action(bundle, action, profile, menus, entry=entry)
    discovered = menu_action(scene, action, menus)
    if discovered:
        return discovered
    for item in activity_actions(bundle, scene, profile):
        if item["id"].rsplit(":", 1)[-1] == action:
            return {key: item[key] for key in ("id", "label", "path", "shortcut")}
    shortcut = ADAPTERS.get(bundle.casefold(), {}).get(scene, {}).get(action)
    if bundle.casefold() in BROWSERS and scene == VIDEO:
        shortcut = {"play": "Space", "backward": "Left", "forward": "Right", "volume-up": "Up", "volume-down": "Down"}.get(action)
    if bundle.casefold() in BROWSERS and scene == MUSIC:
        shortcut = {"volume-up": "Up", "volume-down": "Down"}.get(action)
    if bundle.casefold() in BROWSERS and scene == PDF:
        shortcut = {"previous": "PageUp", "next": "PageDown"}.get(action)
    if not shortcut:
        return None
    identity = "web-video:" + action if bundle.casefold() in BROWSERS and scene == VIDEO else f"scene-default:{scene}:{action}"
    return dict(id=identity, label=action_label(scene, action),
                path=SCENE_LABELS[scene] + " 默认快捷键", shortcut=normalize_shortcut(shortcut))


def catalog_actions(bundle, scene, profiles, *, presentation=""):
    """Action picker catalog for every scene, retaining its existing labels/IDs."""
    if scene in {"regular", PRESENTATION}:
        return scene_actions(bundle, scene, profile=presentation)
    if bundle.casefold() in BROWSERS and scene == VIDEO:
        return video_actions()
    return activity_actions(bundle, scene, profiles.get(scene, ""))
