"""Personal words scoped to one edit, shared by segmentation and spelling clues."""

import re
from collections import defaultdict
from contextlib import contextmanager
from contextvars import ContextVar
from functools import cached_property, lru_cache

import jieba

from .literal import HAN, sound


class EditVocabulary:
    def __init__(self, words):
        self.words = frozenset(words)
        self.han_words = tuple(w for w in words if re.fullmatch(HAN + "+", w))
        self.by_sound = defaultdict(list)
        for word in self.han_words:
            self.by_sound[sound(word)].append(word)

        # Jieba splits spaces and some punctuation before consulting its dictionary.
        # Match mixed-script and multi-part names literally, on Latin word boundaries.
        names = sorted(self.words.difference(self.han_words), key=len, reverse=True)
        patterns = []
        for name in names:
            left = "" if re.fullmatch(HAN, name[0]) else r"(?<![a-zA-Z0-9_])"
            right = "" if re.fullmatch(HAN, name[-1]) else r"(?![a-zA-Z0-9_])"
            patterns.append(left + re.escape(name) + right)
        self.names = re.compile("|".join(patterns)) if patterns else None

    def name_matches(self, text):
        return self.names.finditer(text) if self.names else ()

    @cached_property
    def tokenizer(self):
        if not self.han_words:
            return jieba.dt
        tokenizer = jieba.Tokenizer()
        for word in self.han_words:
            tokenizer.add_word(word)
        return tokenizer

    def cut(self, text):
        start = 0
        for match in self.name_matches(text):
            if start < match.start():
                yield from self.tokenizer.cut(text[start : match.start()])
            yield match.group()
            start = match.end()
        if start < len(text):
            yield from self.tokenizer.cut(text[start:])

    def candidates(self, key, general):
        personal = self.by_sound.get(key)
        ordinary = general.get(key, [])
        return list(dict.fromkeys([*personal, *ordinary])) if personal else ordinary


@lru_cache(maxsize=1)
def _vocabulary(words):
    return EditVocabulary(words)


_empty_vocabulary = EditVocabulary(())
_active = ContextVar("edit_vocabulary", default=None)


@contextmanager
def use_edit_hotwords(words):
    token = _active.set(_vocabulary(tuple(words)) if words else _empty_vocabulary)
    try:
        yield
    finally:
        _active.reset(token)


def current_vocabulary():
    return _active.get() or _empty_vocabulary
