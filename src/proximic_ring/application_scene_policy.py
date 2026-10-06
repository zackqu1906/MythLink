"""Recommended initial scene and explicitly enabled scenes are separate policy.

Capabilities say what an app can open; runtime recognition still proves the
current content. Adding another scene never disables an existing one.
"""
from .scene_capabilities import BROWSERS, MUSIC_APPS, SCENE_LABELS, PRESENTATION, PDF, VIDEO, IMAGE, MUSIC

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
