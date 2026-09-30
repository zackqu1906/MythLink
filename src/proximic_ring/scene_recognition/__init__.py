"""Scene recognition for slides, documents and media.

Runtime callers use ``scene_recognition.engine.detect_scene``. Leaf detectors
share SceneResult, focus validation and document metadata; they never send keys
or read/write gesture bindings. See docs/SCENE_DEFAULTS.md.
"""
from .models import SceneResult

__all__ = ["SceneResult"]
