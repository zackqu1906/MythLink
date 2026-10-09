"""Application identity, declared content types and purpose; never sends keys."""
from __future__ import annotations
from functools import lru_cache
from pathlib import Path
import plistlib
import re
from .models import PRESENTATION, PDF, VIDEO, IMAGE, MUSIC
from .registry import SCENE_LABELS
from .recognition.documents import EXTENSIONS

POWERPOINT = "com.microsoft.Powerpoint"


KNOWN_PROFILES = {
    POWERPOINT.casefold(): "powerpoint", "com.apple.iwork.keynote": "keynote",
    "com.kingsoft.wpsoffice.mac": "wps", "com.kingsoft.wpsoffice.mac.global": "wps",
    "org.libreoffice.script": "libreoffice", "org.openoffice.script": "openoffice",
    "asc.onlyoffice.onlyoffice": "onlyoffice",
}


PRESENTATION_EXTENSIONS = frozenset({"ppt", "pptx", "pptm", "pps", "ppsx", "ppsm", "odp", "key", "dps", "dpt"})


PRESENTATION_UTIS = frozenset({"com.microsoft.powerpoint.ppt", "com.microsoft.powerpoint.pps",
    "org.openxmlformats.presentationml.presentation", "org.openxmlformats.presentationml.slideshow",
    "com.microsoft.powerpoint.openxmlformats.presentationml.presentation", "org.oasis-open.opendocument.presentation",
    "com.apple.keynote.key", "com.apple.iwork.keynote.key"})


def presentation_profile(bundle, metadata=None):
    known = KNOWN_PROFILES.get(bundle.casefold(), "")
    if known:
        return known
    if not isinstance(metadata, dict) or metadata.get("CFBundleIdentifier") != bundle:
        return ""
    for item in metadata.get("CFBundleDocumentTypes", []):
        if not isinstance(item, dict) or str(item.get("CFBundleTypeRole", "")).casefold() != "editor":
            continue  # A chat app that merely accepts PPT attachments is not an editor.
        extensions = {str(ext).casefold() for ext in item.get("CFBundleTypeExtensions", [])}
        utis = {str(uti).casefold() for uti in item.get("LSItemContentTypes", [])}
        type_name = str(item.get("CFBundleTypeName", ""))
        if (extensions & PRESENTATION_EXTENSIONS or utis & PRESENTATION_UTIS
                or re.search(r"\b(?:presentation|slide\s?show)\b|演示文稿|簡報|演示稿", type_name, re.I)):
            return "generic"
    return ""


def installed_presentation_profile(bundle, path):
    known = presentation_profile(bundle)
    if known:
        return known
    app = Path(path)
    if not app.is_absolute() or app.suffix.casefold() != ".app":
        return ""
    try:
        with (app / "Contents/Info.plist").open("rb") as stream:
            return presentation_profile(bundle, plistlib.load(stream))
    except (OSError, ValueError, TypeError):
        return ""


TITLE_NAMES = {
    "powerpoint": ("PowerPoint", "Microsoft PowerPoint"),
    "wps": ("WPS", "WPS Office", "WPS Presentation", "WPS 演示"),
    "keynote": ("Keynote",),
    "libreoffice": ("LibreOffice", "LibreOffice Impress"),
    "openoffice": ("OpenOffice", "OpenOffice Impress"),
    "onlyoffice": ("ONLYOFFICE",),
}


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


def is_music_application(bundle, category=""):
    """Application purpose is independent of the full list of readable files.

    This only disambiguates existing playback evidence; it does not activate a
    scene or consult the user's selected/enabled scene configuration.
    """
    return bundle.casefold() in MUSIC_APPS or (
        bundle.casefold() not in BROWSERS and category == "public.app-category.music")


def application_scene_profiles(bundle, metadata=None):
    profiles = dict(KNOWN.get(bundle.casefold(), {}))
    if bundle.casefold() in BROWSERS:
        profiles.update({scene: "generic" for scene in EXTENSIONS})
    presentation = presentation_profile(bundle, metadata)
    if presentation:
        profiles[PRESENTATION] = presentation
    if isinstance(metadata, dict) and metadata.get("CFBundleIdentifier") == bundle:
        category = {"public.app-category.music": MUSIC, "public.app-category.video": VIDEO,
                    "public.app-category.photography": IMAGE}.get(metadata.get("LSApplicationCategoryType", ""))
        if category:
            profiles.setdefault(category, "generic")
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
def _read_metadata(path, mtime):
    with Path(path).open("rb") as stream:
        return plistlib.load(stream)


def _installed_metadata(bundle, path):
    app = Path(path)
    if app.is_absolute() and app.suffix.casefold() == ".app":
        info = app / "Contents/Info.plist"
        try:
            metadata = _read_metadata(str(info), info.stat().st_mtime_ns)
            if isinstance(metadata, dict) and metadata.get("CFBundleIdentifier") == bundle:
                return metadata
        except (OSError, ValueError, TypeError):
            pass
    return {}


def installed_scene_profiles(bundle, path=""):
    return application_scene_profiles(bundle, _installed_metadata(bundle, path))


def installed_application_category(bundle, path=""):
    return str(_installed_metadata(bundle, path).get("LSApplicationCategoryType", ""))


