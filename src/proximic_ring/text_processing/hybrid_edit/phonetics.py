"""Single-character Mandarin readings, shared by macOS and Windows.

mandarin.txt.gz contains the ICU Mandarin-Latin reading of each code point
U+3400 through U+9FFF in order, with its original tone marks.
"""

import gzip
import unicodedata
from functools import lru_cache
from importlib.resources import files


@lru_cache(maxsize=1)
def _readings():
    with files(__package__).joinpath("mandarin.txt.gz").open("rb") as stream:
        return gzip.decompress(stream.read()).decode("utf-8").splitlines()


def tone(char):
    value = ord(char)
    return _readings()[value - 0x3400] if 0x3400 <= value < 0xA000 else char


def tonal(word):
    return tuple(tone(char) for char in word)


@lru_cache(maxsize=32768)
def reading(char):
    decomposed = unicodedata.normalize("NFD", tone(char)).lower()
    return "".join(c for c in decomposed if not unicodedata.combining(c))
