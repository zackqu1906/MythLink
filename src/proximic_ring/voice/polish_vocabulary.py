"""Shortlist personal spellings for the existing swipe-down polish request."""
from collections import defaultdict
from difflib import SequenceMatcher
from functools import lru_cache
import re
import unicodedata

from ..text_processing.hybrid_edit.phonetics import reading


_HAN = re.compile(r"[\u3400-\u9fff]+")
_LATIN = re.compile(r"[a-zA-Z][a-zA-Z0-9]*")
_ASCII_TOKEN = r"[a-zA-Z0-9_]"
_ENGLISH_TOKEN = re.compile(r"(?<![a-zA-Z0-9_])[a-zA-Z]{3,}(?![a-zA-Z0-9_])")


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


def observed_asr_form(text, form):
    match = _literal(form).search(text)
    return match.group() if match else ""


def polish_heard_form(text, word):
    """Point the model to a specific source span; repeated literal terms can coexist with errors."""
    found = []
    if _HAN.fullmatch(word):
        sound = tuple(map(reading, word))
        for run in _HAN.findall(text):
            syllables = tuple(map(reading, run))
            for i in range(len(run) - len(word) + 1):
                span = run[i:i + len(word)]
                if span != word and syllables[i:i + len(word)] == sound:
                    found.append(span)
    else:
        found.extend(m.group() for m in _literal(word, allow_spaces=True).finditer(text) if m.group() != word)
        if word.isascii() and word.isalpha() and len(word) >= 3:
            found.extend(t for t in _ENGLISH_TOKEN.findall(text) if t != word and _latin_key(t) == _latin_key(word))
    unique = set(found)
    return found[0] if len(unique) == 1 else ""


@lru_cache(maxsize=8192)
def _syllable_similarity(left, right):
    if left == right:
        return 1.0
    retroflex = ("zh", "ch", "sh", "z", "c", "s")
    left_tail = next((left[len(prefix):] for prefix in retroflex if left.startswith(prefix)), None)
    right_tail = next((right[len(prefix):] for prefix in retroflex if right.startswith(prefix)), None)
    if left_tail is not None and right_tail is not None and left_tail and right_tail:
        if left_tail == right_tail:
            return 0.88
        if min(len(left_tail), len(right_tail)) >= 2:
            closeness = SequenceMatcher(None, left_tail, right_tail, autojunk=False).ratio()
            if closeness >= 0.65:
                return 0.64
    if left[0] != right[0]:
        # A different initial alone is a common ASR error.
        if len(left) > 1 and left[1:] == right[1:]:
            return 0.72
        return 0.0
    return SequenceMatcher(None, left, right, autojunk=False).ratio()


def _transliteration_key(spelling):
    # A pinyin syllable and an English syllable may spell a similar affricate differently.
    value = _latin_key(spelling.lower()).replace("ch", "q").replace("sh", "x")
    return re.sub(r"([jqx])u$", r"\1", value)


def _mandarin_matches(word, runs):
    sound = tuple(map(reading, word))
    # For longer terms ASR can omit an interior syllable.
    elided = {sound[:i] + sound[i + 1:] for i in range(1, len(word) - 1)}
    for run, phones in runs:
        for i in range(len(run) - len(word) + 2):
            if phones[i:i + len(word) - 1] in elided:
                yield 0.85, run[i:i + len(word) - 1]
            fragment = phones[i:i + len(word)]
            span = run[i:i + len(word)]
            if len(fragment) != len(word) or span == word:
                continue
            scores = [_syllable_similarity(a, b) for a, b in zip(fragment, sound)]
            if len(scores) >= 3 and scores.count(1.0) == len(scores) - 1:
                yield 0.89, span
            elif min(scores) >= 0.42 and (1.0 in scores or min(scores) >= 0.84):
                yield sum(scores) / len(scores), span


def _english_spans(text):
    for run in re.finditer(r"(?<![A-Za-z0-9_])[A-Za-z]+(?:[ \t]+[A-Za-z]+)*(?![A-Za-z0-9_])", text):
        tokens = list(re.finditer("[A-Za-z]+", run[0]))
        for i, token in enumerate(tokens):
            for end in tokens[i:i + 3]:
                yield run[0][token.start():end.end()]


def _spelling_matches(key, forms):
    for spelling, span in forms:
        if key[0] != spelling[0] or abs(len(spelling) - len(key)) > 2:
            continue
        yield SequenceMatcher(None, spelling, key, autojunk=False).ratio(), span


def nearby_forms(text, words):
    """A short list of plausible surface/replacement pairs for the model to review."""
    sample = text[:512]
    runs = [(s, tuple(map(reading, s))) for s in _HAN.findall(sample)]
    english = [(_latin_key("".join(span.split())), span) for span in _english_spans(sample)]
    phonetic = [(_transliteration_key("".join(phones[i:i + n])), run[i:i + n]) for run, phones in runs
                for n in (2, 3) for i in range(len(run) - n + 1)]
    mixed = re.findall(r"(?<![A-Za-z0-9_])([A-Z]{1,2}[ \t]+[一二三四五六七八九十])(?![十百千万])", sample)
    suggestions = []
    for word in words:
        if _HAN.fullmatch(word) and 2 <= len(word) <= 10:
            choices = _mandarin_matches(word, runs)
            threshold = 0.78
        elif _LATIN.fullmatch(word) and len(word) >= 3:
            choices = list(_spelling_matches(_latin_key(word), english))
            threshold = 0.66 if word.isupper() and len(word) == 3 else 0.8
            if not word.isupper() and 4 <= len(word) <= 12:
                choices.extend(pair for pair in _spelling_matches(_transliteration_key(word), phonetic)
                               if pair[0] >= 0.86)
            if word.isupper():
                choices.extend((0.71, span) for span in mixed if span[0] == word[0])
        else:
            continue
        best = max((item for item in choices if item[1] != word), key=lambda item: item[0], default=None)
        if best and best[0] >= threshold:
            suggestions.append((*best, word))
    suggestions.sort(key=lambda item: -item[0])
    return [(old, new) for _, old, new in suggestions[:6]]


@lru_cache(maxsize=1)
def warm_polish_segmenter():
    """Start loading the segmenter during speech, before the first swipe needs it."""
    from threading import Thread

    def load():
        import jieba
        jieba.initialize()

    Thread(target=load, name="PolishWordBoundaries", daemon=True).start()


def _word_interiors(text):
    import jieba

    return {offset for word, start, end in jieba.tokenize(text, HMM=False)
            if len(word) >= 2 and _HAN.fullmatch(word) for offset in range(start + 1, end)}


def _spelled_character(text, heard, word):
    if len(heard) != len(word):
        return False
    return any(c != d and any(c in cue for cue in re.findall(r"([\u3400-\u9fff]{1,5})的" + re.escape(c), text))
               for c, d in zip(heard, word) if _HAN.fullmatch(c))


def preserve_spelled_forms(text, output, candidates):
    """Restore an explicitly spelt character if the model still changed that very occurrence."""
    if "的" not in text or text == output:
        return output
    for word, known in candidates:
        heard = observed_asr_form(text, known) if known else polish_heard_form(text, word)
        if not heard or not _spelled_character(text, heard, word):
            continue
        if text.count(heard) != 1 or heard in output or word in text or output.count(word) != 1:
            continue
        left, _, right = text.partition(heard)
        edited_left, _, edited_right = output.partition(word)
        # Verify the surrounding text so a separate mention of the hotword stays untouched.
        left_anchor = "".join(c for c in left if c.isalnum())[-4:]
        right_anchor = "".join(c for c in right if c.isalnum())[:4]
        if ((left_anchor and "".join(c for c in edited_left if c.isalnum()).endswith(left_anchor))
                or (right_anchor and "".join(c for c in edited_right if c.isalnum()).startswith(right_anchor))):
            output = edited_left + heard + edited_right
    return output


def draft_protections(text, options, exact, vocabulary):
    """Keep quoted spellings, explicit contrasts and fragments cut out of an existing word."""
    if not options:
        return ()
    protected = set()
    quoted = re.findall(r'“([^”]+)”|「([^」]+)」|‘([^’]+)’|"([^"]+)"', text)
    literals = {part for group in quoted for part in group if part}
    exact_heard = {heard for heard, _ in exact}
    needs_boundaries = any(old not in exact_heard and _HAN.fullmatch(old) for old, _ in options)
    interiors = _word_interiors(text[:512]) if needs_boundaries else set()
    literal_words = [w for w in vocabulary if _HAN.fullmatch(w) and w in text]
    for heard, word in options:
        # An explicitly spelt character gives stronger evidence than a homophonous entry.
        spellout = _spelled_character(text, heard, word)
        term = rf"(?:{re.escape(heard)}|{re.escape(word)})"
        contrast = word in text and re.search(rf"(?:不是|而非)\s*{term}|错写|听成|改成|拼作|原字|原词", text)
        spans = list(_literal(heard).finditer(text))
        cuts_word = spans and all(m.start() in interiors or m.end() in interiors for m in spans)
        embedded = any(heard != known and heard in known for known in literal_words)
        if heard in literals or spellout or contrast or (heard not in exact_heard and (cuts_word or embedded)):
            protected.add(heard)
    return tuple(sorted(protected))


@lru_cache(maxsize=2048)
def _initial_final(phone):
    match = re.fullmatch(r"(zh|ch|sh|[bpmfdtnlgkhjqxrzcsyw])?(.*)", phone)
    return match[1] or "", match[2].replace("ao", "au")


def _syllable_keys(phone):
    initial, final = _initial_final(phone)
    return tuple(k for k in (initial and "i:" + initial, final and "f:" + final) if k)


def _review_similarity(left, right):
    if left == right:
        return 1.0
    a, x = _initial_final(left)
    b, y = _initial_final(right)
    if x == y:
        return 0.85
    if a and a == b and set(x) & set(y) & set("aeiouv"):
        return 0.70
    return 0.0


def _consonants(spelling):
    folded = _latin_key(spelling).replace("ng", "n")
    return re.sub("[aeiouv]", "", folded)


@lru_cache(maxsize=4)
def _review_index(words):
    pairs, singles, english, consonants = (defaultdict(list) for _ in range(4))
    for word in words:
        if _HAN.fullmatch(word) and len(word) == 2:
            first, second = map(reading, word)
            for a in _syllable_keys(first):
                for b in _syllable_keys(second):
                    pairs[a, b].append((word, first, second))
        elif _HAN.fullmatch(word) and len(word) == 1:
            singles[reading(word)].append(word)
        elif _ENGLISH_TOKEN.fullmatch(word):
            english[word[0].lower()].append(word)
            key = _consonants(word)
            if len(key) >= 3:
                consonants[key].append(word)
    return pairs, singles, english, consonants


def review_forms(text, words):
    """Weak acoustic evidence nominates a span for review, never for the rule draft."""
    pairs, singles, english, consonants = _review_index(tuple(words))
    sample = text[:512]
    ranked = {}
    for run in _HAN.findall(sample):
        phones = list(map(reading, run))
        for i, phone in enumerate(phones):
            for word in singles.get(phone, ()):
                if run[i] != word:
                    ranked[run[i], word] = 1.0
            if i + 1 == len(phones):
                continue
            second = phones[i + 1]
            span = run[i:i + 2]
            for a in _syllable_keys(phone):
                for b in _syllable_keys(second):
                    for word, x, y in pairs.get((a, b), ()):
                        if span == word or (_initial_final(phone)[0] != _initial_final(x)[0]
                                            and _initial_final(second)[0] != _initial_final(y)[0]):
                            continue
                        score = (_review_similarity(phone, x) + _review_similarity(second, y)) / 2
                        if score >= 0.77:
                            ranked[span, word] = score
            for word in consonants.get(_consonants(phone + second), ()):
                ranked[span, word] = 0.86
    if _HAN.search(sample):
        for token in _ENGLISH_TOKEN.findall(sample):
            names = english.get(token[0].lower(), ())
            if len(names) <= 2 and all(token.casefold() != name.casefold() for name in names):
                for word in names:
                    ranked[token, word] = 0.76
    return sorted(ranked, key=ranked.get, reverse=True)[:12]


def alternative_spellings(text, options, words):
    """Surface less certain alternatives for semantic review without changing the draft."""
    alternatives = []
    han_words = [(word, tuple(map(reading, word))) for word in words if len(word) == 2 and _HAN.fullmatch(word)]
    for heard, nominated in options:
        if len(heard) != 2 or not _HAN.fullmatch(heard):
            continue
        sound = tuple(map(reading, heard))
        alternatives.extend((heard, word) for word, phones in han_words
                            if word not in (heard, nominated) and (phones[0] == sound[0] or phones[1] == sound[1]))
    return list(dict.fromkeys(alternatives))[:8]


def correction_draft(text, options, confirmed=()):
    replacements = {}
    for heard, word in options:
        replacements.setdefault(heard, set()).add(word)
    learned = {}
    for heard, word in confirmed:
        learned.setdefault(heard, set()).add(word)
    replacements.update(learned)
    # Retain the original for ambiguous readings. Work on the original once, on token boundaries.
    patterns = [_literal(span).pattern for span in sorted(replacements, key=len, reverse=True)
                if len(replacements[span]) == 1]
    if not patterns:
        return text
    return re.sub("|".join(patterns), lambda m: next(iter(replacements[m[0]])), text)


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
        latin = {_latin_key(token) for token in _ENGLISH_TOKEN.findall(text)}
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
