"""Recognize a complete spoken choice without discarding extra answer text."""
import re
import unicodedata


def _normal(text):
    text = unicodedata.normalize('NFKC', text).casefold().strip()
    return re.sub(r'[\s，,。.!！?？、:：;；]+', '', text)


def _number(text):
    words = {'one': 1, 'first': 1, 'two': 2, 'second': 2, 'three': 3,
             'third': 3, 'four': 4, 'fourth': 4, 'five': 5, 'fifth': 5}
    if text in words:
        return words[text]
    if text.isascii() and text.isdigit():
        return int(text)
    digits = {'零': 0, '一': 1, '二': 2, '两': 2, '三': 3, '四': 4,
              '五': 5, '六': 6, '七': 7, '八': 8, '九': 9}
    if text in digits:
        return digits[text]
    if re.fullmatch('[一二三四五六七八九]?十[一二三四五六七八九]?', text):
        tens, units = text.split('十')
        return digits.get(tens, 1) * 10 + digits.get(units, 0)
    return None


def match_choice(text, options):
    """Return the offered index, or None for a free answer. Invalid numbers raise.

    Only a whole choice phrase is accepted. Negation, multiple numbers and
    additional requirements remain free text, never a fuzzy permission grant.
    Display numbering is one-based even when the protocol indices have gaps.
    """
    if not options:
        return None
    normalized = _normal(text)
    number = None
    match = re.fullmatch(
        r'(?:(?:我想|我要|我|请|帮我|就)?(?:选择|选)|选项|我要|就)?第?'
        r'([0-9]+|[零一二两三四五六七八九十]+)(?:个选项|号选项|选项|个|项|号)?(?:吧|谢谢)?',
        normalized)
    if not match:
        match = re.fullmatch(r'(?:please)?(?:(?:ichoose|choose|select|pick))?(?:the)?(?:option)?'
                             r'([0-9]+|one|two|three|four|five|first|second|third|fourth|fifth)'
                             r'(?:option|one)?(?:please)?', normalized)
    if match:
        number = _number(match[1])
    if number is not None:
        if not 1 <= number <= len(options):
            raise ValueError(f'请选择 1–{len(options)} 中的一个编号，或直接回答问题。')
        selected = options[number - 1]
    else:
        # A unique full label is useful for "Safari" and "始终允许" too.
        label_text = re.sub(r'^(?:(?:我想|我要|我|请|帮我|就)?(?:选择|选)|ichoose|choose|select|pick)', '', normalized)
        labels = [o for o in options if _normal(re.sub(r'[（(](?:推荐|recommended)[）)]', '', o['label'], flags=re.I)) in {normalized, label_text}]
        if len(labels) != 1:
            return None
        selected = labels[0]
    if not selected['enabled']:
        raise ValueError(selected.get('reason') or '这个选项当前不可用，请重新回答。')
    return selected['index']
