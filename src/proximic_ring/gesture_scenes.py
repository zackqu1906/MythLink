"""Compatibility exports; scene implementations now live in proximic_ring.scenes."""
from .scenes.models import PRESENTATION
from .scenes.registry import SCENE_ANCHORS
from .scenes.capabilities import (POWERPOINT, KNOWN_PROFILES, PRESENTATION_EXTENSIONS,
    PRESENTATION_UTIS, presentation_profile, installed_presentation_profile, TITLE_NAMES)
from .scenes.adapters.presentation import POWERPOINT_ACTIONS, NAVIGATION_ACTIONS, PROFILES, scene_actions
