"""Shared file-type evidence, independent of applications and shortcut presets."""
from pathlib import PurePosixPath
from urllib.parse import unquote, urlsplit

from .models import PDF, VIDEO, IMAGE, MUSIC
EXTENSIONS = {
    PDF: {"pdf"},
    VIDEO: {"mp4", "m4v", "mov", "mkv", "avi", "webm", "wmv", "flv", "mpg", "mpeg", "ts", "m2ts", "3gp"},
    IMAGE: {"png", "jpg", "jpeg", "gif", "heic", "heif", "tif", "tiff", "bmp", "webp", "avif", "svg", "raw", "cr2", "nef"},
    MUSIC: {"mp3", "m4a", "aac", "wav", "aif", "aiff", "flac", "ogg", "opus", "wma", "alac", "ape"},
}


def document_suffix(value):
    try:
        return PurePosixPath(unquote(urlsplit(str(value or "")).path)).suffix.casefold().lstrip(".")
    except (TypeError, ValueError):
        return ""


def document_kind(value):
    suffix = document_suffix(value)
    return next((scene for scene, extensions in EXTENSIONS.items() if suffix in extensions), "")


def non_slide_document(value):
    return bool(document_kind(value) or document_suffix(value) in {
        "doc", "docx", "docm", "odt", "rtf", "txt", "md", "xls", "xlsx", "xlsm", "ods", "csv"})
