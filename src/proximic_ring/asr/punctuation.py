"""Local formatting of a complete dictation, after ASR has finished."""
from __future__ import annotations

import re


_HAN = "\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\U00020000-\U000323af"
# One Han character, number (including decimals), or word. Apostrophes and
# hyphens inside English words do not create additional units.
_UNITS = re.compile(
    rf"[{_HAN}]|\d+(?:[.,]\d+)*|[^\W_{_HAN}]+(?:['’\-][^\W_{_HAN}]+)*"
)
_END_PUNCTUATION = "。！？!?.,，、；;：:…"
# An ASCII punctuation suffix can be meaningful in a URL or abbreviation.
# Keep ambiguous endings intact instead of damaging the recognized token.
_PROTECTED = re.compile(
    r"(?:[a-z][a-z0-9+.-]*://|www\.)[^\s。！？…，、；：“”‘’（）《》〈〉【】]+"
    r"|(?:[a-z]\.){2,}",
    re.IGNORECASE,
)


def trim_short_utterance_end(text: str, *, max_units: int = 20) -> str:
    """Remove only trailing sentence punctuation from a short final result.

    Count the entire utterance, not each server segment. Partial results must
    remain untouched because the speaker can continue past the length limit.
    Closing quotes/brackets, symbols and punctuation inside text stay intact.
    """
    if not text or text[-1] not in _END_PUNCTUATION:
        return text
    if not 0 < len(_UNITS.findall(text)) <= max_units:
        return text
    protected = [(match.start(), match.end()) for match in _PROTECTED.finditer(text)]
    end = len(text)
    while end and text[end - 1] in _END_PUNCTUATION:
        if any(start <= end - 1 < stop for start, stop in protected):
            break
        end -= 1
    return text[:end]
