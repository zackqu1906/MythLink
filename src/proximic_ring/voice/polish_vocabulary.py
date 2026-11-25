"""Shortlist personal spellings for the existing swipe-down polish request."""
import re
import unicodedata

from ..text_processing.hybrid_edit.phonetics import reading


_HAN = re.compile(r"[\u3400-\u9fff]+")
_LATIN = re.compile(r"[a-zA-Z][a-zA-Z0-9]*")
_ASCII_TOKEN = r"[a-zA-Z0-9_]"


def _literal(word, *, allow_spaces=False):
    left = r"(?<![a-zA-Z0-9_])" if re.fullmatch(_ASCII_TOKEN, word[0]) else ""
    right = r"(?![a-zA-Z0-9_])" if re.fullmatch(_ASCII_TOKEN, word[-1]) else ""
    pattern = re.escape(word)
    if allow_spaces and re.fullmatch(r"[a-zA-Z0-9]+(?: [a-zA-Z0-9]+)*", word):
        pattern = r"[ \t]*".join(map(re.escape, word.replace(" ", "")))
    return re.compile(left + pattern + right, re.IGNORECASE | re.ASCII)


def _sound(word):
    return "/" + "/".join(map(reading, word)) + "/"


def _latin_key(word):
    # Broad ASR confusions for English names. Only used to nominate candidates.
    folded = word.lower().replace("th", "s").replace("ph", "f").replace("ck", "k").replace("y", "i")
    return re.sub(r"(.)\1+", r"\1", folded)


def confirmed_asr_form(before, after, word):
    """Only learn the old token when replacing it explains the entire edit."""
    if after.count(word) != 1:
        return ""
    left, _, right = after.partition(word)
    if not before.startswith(left) or not before.endswith(right):
        return ""
    start, end = len(left), len(before) - len(right)
    old = before[start:end]
    if not 2 <= len(old) <= 40 or old == word:
        return ""
    if not (_LATIN.fullmatch(old) or _HAN.fullmatch(old)) or not _literal(old).match(before, start):
        return ""
    if not _literal(word).match(after, start):
        return ""
    if _HAN.fullmatch(old):
        if _HAN.fullmatch(word) and _sound(old) != _sound(word):
            return ""
        if not _HAN.fullmatch(word) and len(old) > 6:
            return ""
    return old


class PolishVocabulary:
    """Immutable search index rebuilt on vocabulary changes, never on a gesture."""

    def __init__(self, entries):
        self.rows = []
        for entry in entries:
            word = entry["word"]
            if not entry["enabled"] or len(word) < 2:
                continue
            forms = dict.fromkeys(entry.get("asr_forms", {}).values())
            known = [(form, _literal(form)) for form in forms]
            sound = _sound(word) if _HAN.fullmatch(word) else ""
            latin_key = _latin_key(word) if word.isascii() and word.isalpha() and len(word) >= 3 else ""
            self.rows.append((word, known, _literal(word, allow_spaces=True), sound, latin_key))
        self.has_han = any(row[3] for row in self.rows)

    def candidates(self, text):
        if not self.rows or not text:
            return ()
        text = unicodedata.normalize("NFKC", text)
        sounds = " ".join(_sound(run) for run in _HAN.findall(text)) if self.has_han else ""
        latin = {_latin_key(token) for token in re.findall(r"(?<![a-zA-Z0-9_])[a-zA-Z]{3,}(?![a-zA-Z0-9_])", text)}
        matches = []
        for word, known, literal, sound, latin_key in self.rows:
            form = next((form for form, pattern in known if pattern.search(text)), "")
            if form:
                rank = 0
            elif literal.search(text) or (sound and sound in sounds):
                rank = 1
            elif latin_key and latin_key in latin:
                rank = 2
            else:
                continue
            matches.append((rank, word, form))
        matches.sort(key=lambda row: row[0])  # User order breaks ties.
        return tuple((word, form) for _, word, form in matches[:6])
