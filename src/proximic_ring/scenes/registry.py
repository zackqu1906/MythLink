"""One catalog for all scene meanings; no app IDs, shortcuts or native APIs."""
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Mapping
from .models import PRESENTATION, PDF, IMAGE, VIDEO, MUSIC

SCENE_ANCHORS = frozenset({"index-pinch", "middle-pinch"})

@dataclass(frozen=True)
class SceneDefinition:
    label: str
    gestures: Mapping[str, tuple[str, ...]]
    entry_gestures: Mapping[str, tuple[str, ...]] = field(default_factory=lambda: MappingProxyType({}))
    # Preserve each scene's existing resolution and on-disk lifecycle policy.
    resolution: str = "menu_first"
    pending_format: str = "per_gesture"
    storage_order: int = 0


def gestures(**items):
    return MappingProxyType({key.replace('_', '-'): (value,) if isinstance(value, str) else value
                             for key, value in items.items()})


SCENES = MappingProxyType({
    PRESENTATION: SceneDefinition("放映", gestures(swipe_left="previous", swipe_right="next", snap="end"),
        gestures(snap=("start-current", "start-first")), resolution="preset_first", pending_format="pendingStart"),
    PDF: SceneDefinition("PDF 阅读", gestures(swipe_left="previous", swipe_right="next")),
    VIDEO: SceneDefinition("视频播放", gestures(tap="play", swipe_left="backward", swipe_right="forward",
        circle_clockwise="volume-up", circle_counterclockwise="volume-down"), storage_order=2),
    IMAGE: SceneDefinition("图片预览", gestures(swipe_left="previous", swipe_right="next"), storage_order=1),
    MUSIC: SceneDefinition("音乐播放", gestures(tap="play", swipe_left="previous", swipe_right="next",
        swipe_up="volume-up", swipe_down="volume-down"), storage_order=3),
})
SCENE_LABELS = {scene: definition.label for scene, definition in SCENES.items()}
# Keep legacy serialized initializedScenes ordering and pendingStart separate.
PENDING_SCENES = tuple(sorted((scene for scene, definition in SCENES.items()
                               if definition.pending_format == "per_gesture"),
                              key=lambda scene: SCENES[scene].storage_order))
PENDING_TEMPLATES = {scene: {gesture: choices[0] for gesture, choices in SCENES[scene].gestures.items()}
                     for scene in PENDING_SCENES}
PRESENTATION_GESTURES = {"regular": dict(SCENES[PRESENTATION].entry_gestures),
                         PRESENTATION: dict(SCENES[PRESENTATION].gestures)}

LABELS = {
    PDF: {"previous": "上一页", "next": "下一页"},
    IMAGE: {"previous": "上一张", "next": "下一张"},
    MUSIC: {"previous": "上一首", "next": "下一首"},
    VIDEO: {"backward": "后退", "forward": "快进"},
}
COMMON_LABELS = {"play": "播放 / 暂停", "volume-up": "提高音量", "volume-down": "降低音量"}

def action_label(scene, action):
    return LABELS.get(scene, {}).get(action, COMMON_LABELS.get(action, action))
