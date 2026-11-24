"""Thinking budgets from the 24/128 spoken-edit evaluation."""
import re


_COMPOUND = re.compile(r"然后|再(?:把|将|改|加|向|去|润色|翻)|不是|算了|照旧|两[行处件]|分[行开]|[。！？].{2,}")
_SPELLING = re.compile(r"的[一-龥]|部首|偏旁|拼[写音]|[A-Za-z]{2,}.+的|[一-龥]是.{1,8}[一-龥]的")
_QUOTES = frozenset('“”‘’「」『』"')
_VERBATIM = re.compile(r"引号|原样引用|照录|逐字")


def text_thinking_budget(character_count: int) -> int:
    return 128 if character_count >= 100 else 24


def edit_thinking_budget(utterance: str, editable_text: str) -> int | None:
    if _VERBATIM.search(utterance) or not _QUOTES.isdisjoint(utterance) or not _QUOTES.isdisjoint(editable_text):
        return None
    if _COMPOUND.search(utterance) or _SPELLING.search(utterance):
        return 128
    return text_thinking_budget(len(editable_text))
