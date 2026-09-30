"""Common recognition result, independent of Qt and macOS bridge objects."""
from dataclasses import dataclass, field

PRESENTATION, PDF, IMAGE, VIDEO, MUSIC = "presentation", "pdf", "image", "video", "music"


@dataclass(frozen=True)
class SceneResult:
    scene: str = ""
    input_context: str = "unknown"
    website: str = ""
    page_key: str = ""
    web_area: object = field(default=None, repr=False)
    player: object = field(default=None, repr=False)
