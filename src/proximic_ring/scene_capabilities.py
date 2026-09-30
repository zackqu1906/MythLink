"""Scene availability from application identity and declared document support.

Availability exposes an editor tab, never activates an override at runtime.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
import plistlib

from .gesture_scenes import PRESENTATION, presentation_profile
from .scene_recognition.documents import EXTENSIONS

from .scene_recognition.models import PDF, VIDEO, IMAGE, MUSIC
SCENE_LABELS = {PRESENTATION: "放映", PDF: "PDF 阅读", VIDEO: "视频播放", IMAGE: "图片预览", MUSIC: "音乐播放"}

UTIS = {
    PDF: {"com.adobe.pdf"},
    VIDEO: {"public.movie", "public.video", "public.mpeg-4", "public.mpeg", "com.apple.quicktime-movie", "org.matroska.mkv"},
    IMAGE: {"public.image", "public.jpeg", "public.png", "public.tiff", "public.heic", "public.heif", "com.compuserve.gif", "org.webmproject.webp"},
    MUSIC: {"public.audio", "public.mp3", "public.mpeg-4-audio", "public.aiff-audio", "com.microsoft.waveform-audio", "org.xiph.flac"},
}
KNOWN = {
    "com.apple.preview": {PDF: "preview", IMAGE: "preview"},
    "com.adobe.reader": {PDF: "generic"}, "com.adobe.acrobat.pro": {PDF: "generic"},
    "com.readdle.pdfexpert-mac": {PDF: "generic"}, "net.sourceforge.skim-app.skim": {PDF: "generic"},
    "com.apple.ibooksx": {PDF: "generic"},
    "com.kingsoft.wpsoffice.mac": {PDF: "generic"}, "com.kingsoft.wpsoffice.mac.global": {PDF: "generic"},
    "com.apple.quicktimeplayerx": {VIDEO: "quicktime", MUSIC: "quicktime"},
    "org.videolan.vlc": {VIDEO: "generic", MUSIC: "generic"},
    "com.colliderli.iina": {VIDEO: "generic", MUSIC: "generic"},
    "com.apple.tv": {VIDEO: "generic"},
    "com.apple.photos": {IMAGE: "generic", VIDEO: "generic"},
    "com.apple.finder": {PDF: "generic", IMAGE: "generic", VIDEO: "generic", MUSIC: "generic"},
    "com.apple.music": {MUSIC: "music"}, "com.apple.itunes": {MUSIC: "music"},
    "com.spotify.client": {MUSIC: "generic"}, "com.netease.163music": {MUSIC: "generic"},
    "com.tencent.qqmusic": {MUSIC: "generic"}, "com.kugou.macmusic": {MUSIC: "generic"},
    "com.apple.podcasts": {MUSIC: "generic"}, "com.apple.voicememos": {MUSIC: "generic"},
}
BROWSERS = {"com.apple.safari", "com.google.chrome", "com.google.chrome.canary", "com.microsoft.edgemac",
            "org.mozilla.firefox", "company.thebrowser.browser", "com.brave.browser", "com.operasoftware.opera"}
MUSIC_APPS = {bundle for bundle, modes in KNOWN.items() if set(modes) == {MUSIC}}


def application_scene_profiles(bundle, metadata=None):
    profiles = dict(KNOWN.get(bundle.casefold(), {}))
    if bundle.casefold() in BROWSERS:
        profiles.update({scene: "generic" for scene in EXTENSIONS})
    presentation = presentation_profile(bundle, metadata)
    if presentation:
        profiles[PRESENTATION] = presentation
    if isinstance(metadata, dict) and metadata.get("CFBundleIdentifier") == bundle:
        declarations = {}
        for key in ("UTExportedTypeDeclarations", "UTImportedTypeDeclarations"):
            for item in metadata.get(key, []):
                if isinstance(item, dict):
                    parents = item.get("UTTypeConformsTo", [])
                    declarations[str(item.get("UTTypeIdentifier", "")).casefold()] = [parents] if isinstance(parents, str) else parents
        for item in metadata.get("CFBundleDocumentTypes", []):
            if not isinstance(item, dict) or str(item.get("CFBundleTypeRole", "")).casefold() not in {"editor", "viewer"}:
                continue
            extensions = {str(ext).casefold().lstrip(".") for ext in item.get("CFBundleTypeExtensions", [])}
            types = {str(uti).casefold() for uti in item.get("LSItemContentTypes", [])}
            for _ in range(8):
                more = {str(parent).casefold() for uti in types for parent in declarations.get(uti, [])}
                if more <= types:
                    break
                types |= more
            for scene in EXTENSIONS:
                if extensions & EXTENSIONS[scene] or types & UTIS[scene]:
                    profiles.setdefault(scene, "generic")
    return {scene: profiles[scene] for scene in SCENE_LABELS if scene in profiles}


@lru_cache(maxsize=256)
def _read_profiles(bundle, path, mtime):
    with Path(path).open("rb") as stream:
        return application_scene_profiles(bundle, plistlib.load(stream))


def installed_scene_profiles(bundle, path=""):
    app = Path(path)
    if app.is_absolute() and app.suffix.casefold() == ".app":
        info = app / "Contents/Info.plist"
        try:
            return dict(_read_profiles(bundle, str(info), info.stat().st_mtime_ns))
        except (OSError, ValueError, TypeError):
            pass
    return application_scene_profiles(bundle)


# Apple Support: Preview cpprvw0003, Music mus1019, QuickTime qtpa4808515d.
# Other applications use their discovered menus or a user-recorded shortcut.
PRESETS = {
    ("preview", PDF): [("previous", "上一页", "Alt+Up"), ("next", "下一页", "Alt+Down"),
                         ("fullscreen", "进入 / 退出全屏", "Cmd+Ctrl+F")],
    ("preview", IMAGE): [("previous", "上一张 / 向上翻页", "PageUp"), ("next", "下一张 / 向下翻页", "PageDown"),
                           ("actual", "实际大小", "Cmd+Alt+0"), ("fit", "适合窗口", "Cmd+Alt+9")],
    ("music", MUSIC): [("play", "播放 / 暂停", "Space"), ("previous", "上一首", "Left"), ("next", "下一首", "Right"),
                         ("volume-up", "提高音量", "Cmd+Up"), ("volume-down", "降低音量", "Cmd+Down")],
    ("quicktime", VIDEO): [("play", "播放 / 暂停", "Space"), ("backward", "提高倒放速度", "Cmd+Left"),
                            ("forward", "提高快进速度", "Cmd+Right"), ("fullscreen", "进入 / 退出全屏", "Cmd+F")],
    ("quicktime", MUSIC): [("play", "播放 / 暂停", "Space"), ("backward", "提高倒放速度", "Cmd+Left"),
                            ("forward", "提高快进速度", "Cmd+Right")],
}


def activity_actions(bundle, scene, profile=""):
    profile = application_scene_profiles(bundle).get(scene, profile)
    return [dict(id=f"{profile}:{scene}:{key}", label=label, shortcut=shortcut,
                 path=SCENE_LABELS[scene] + " 快捷键", available=None, preset=True)
            for key, label, shortcut in PRESETS.get((profile, scene), [])]
