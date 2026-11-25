"""Verbatim task prompts shipped with the voice editor."""

from functools import lru_cache
from importlib.resources import files


@lru_cache
def prompt(name: str) -> str:
    return files(__package__).joinpath("prompts", name + ".txt").read_text(encoding="utf-8")
