"""Recommended/enabled scene policy; independent of current runtime evidence."""
from .capabilities import BROWSERS, MUSIC_APPS
from .registry import SCENE_LABELS
from .models import PRESENTATION, PDF, VIDEO, IMAGE, MUSIC

PRIMARY = {
    'com.apple.preview': PDF, 'com.apple.photos': IMAGE, 'com.apple.finder': IMAGE,
    'com.apple.quicktimeplayerx': VIDEO, 'org.videolan.vlc': VIDEO,
    'com.colliderli.iina': VIDEO,
    **{bundle: MUSIC for bundle in MUSIC_APPS},
    **{bundle: VIDEO for bundle in BROWSERS},
}


CATEGORIES = {'public.app-category.music': MUSIC, 'public.app-category.video': VIDEO,
              'public.app-category.photography': IMAGE}


def primary_scene(bundle, profiles, preferred='', category=''):
    if preferred in profiles:
        return preferred
    # Known music players may also advertise video/images in their metadata.
    # Their main purpose wins over file-type enumeration order.
    for scene in (PRIMARY.get(bundle.casefold()), CATEGORIES.get(category), PRESENTATION):
        if scene in profiles:
            return scene
    return next((scene for scene in SCENE_LABELS if scene in profiles), 'regular')


def scene_choices(profiles):
    return [scene for scene in SCENE_LABELS if scene in profiles]


def enabled_profiles(bundle, profiles, preferred='', enabled=None):
    if enabled is None:
        enabled = [primary_scene(bundle, profiles, preferred)]
        if bundle.casefold() in BROWSERS:
            enabled += [PDF, VIDEO]  # Mixed content is normal within one browser.
    return {scene: profiles[scene] for scene in SCENE_LABELS if scene in profiles and scene in enabled}


