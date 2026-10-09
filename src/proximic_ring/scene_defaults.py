"""Compatibility exports preserving the legacy four-scene pending API.

New callers use scenes.defaults.scene_defaults for all five scene definitions.
"""
from .scenes.registry import PENDING_TEMPLATES as TEMPLATES, LABELS, COMMON_LABELS, action_label
from .scenes.defaults import SCENE_DEFAULTS_VERSION, pending_scene_defaults as scene_defaults
from .scenes.resolver import resolve_action as _resolve_action
from .scenes.menus import menu_action, _label
from .scenes.adapters.native import ADAPTERS
from .scenes.migrations import LEGACY_MUSIC_VOLUME, migrate_music_volume_defaults
from .scenes.models import PDF, IMAGE, VIDEO, MUSIC
from .scenes.capabilities import BROWSERS


def resolve_action(bundle, scene, action, profile="", menus=()):
    return _resolve_action(bundle, scene, action, profile, menus) if scene in TEMPLATES else None
