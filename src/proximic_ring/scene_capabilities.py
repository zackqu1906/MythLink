"""Compatibility exports for canonical scene capabilities and native catalogs."""
from .scenes.capabilities import (PRESENTATION, PDF, VIDEO, IMAGE, MUSIC, SCENE_LABELS,
    EXTENSIONS, UTIS, KNOWN, BROWSERS, MUSIC_APPS, is_music_application,
    application_scene_profiles, _read_metadata, _installed_metadata,
    installed_scene_profiles, installed_application_category, presentation_profile)
from .scenes.adapters.native import PRESETS, activity_actions
