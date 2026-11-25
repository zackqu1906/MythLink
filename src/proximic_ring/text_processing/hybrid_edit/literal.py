"""Optional deterministic fast path for unambiguous spoken edits; return None on ambiguity."""

import re
from .phonetics import reading

HAN = r"[\u3400-\u9fff]"
SYMBOLS = {
    "逗号": "，",
    "句号": "。",
    "问号": "？",
    "感叹号": "！",
    "叹号": "！",
    "冒号": "：",
    "分号": "；",
    "顿号": "、",
}
QUALIFIER = re.compile(
    r"第[一二三四五六七八九十几0-9]|那位|那边|只[改动]|不要动|别动|不动的|不变|引号里|前一个|后一个|第一个|最后一个|第一处|第二处"
)
EXTRA = re.compile(
    r"翻译|翻成|译成|分行|分开写|两行|两条|格式|润色|扩写|缩写|改写|改成.{0,3}(?:文|语)|后(?:面|边)(?:那|这|一)句|另[外一]|再把|再加|加个|末尾加|原样|照录|台词|什么意思|一句话解释"
)
CLOSING = "。，、！？；,.!?;　 "


def phrase(s):
    return s.strip(CLOSING).strip('“”‘’「」『』"').strip()


def sound(s):
    return tuple(reading(c) for c in s)


def spoken_symbol(s):
    return SYMBOLS.get(s, s)


def place(text, old, value):
    return text.replace(old, value)


def target_span(text, named, spelling):
    if named and named in text:
        return named
    needles = [x for x in (named, spelling) if x and re.fullmatch(HAN + "+", x)]
    for needle in needles:
        n = len(needle)
        pron = sound(needle)
        segments = {
            text[j : j + n]
            for j in range(len(text) - n + 1)
            if re.fullmatch(HAN + "{" + str(n) + "}", text[j : j + n])
        }
        matches = {seg for seg in segments if sound(seg) == pron}
        if len(matches) == 1:
            return next(iter(matches))
    return None


def parse_glyph(u, text):
    if (
        EXTRA.search(u)
        or QUALIFIER.search(u)
        or re.search(r"接着|分两|前面的|后面的|然后|末尾|材料|写一段", u)
    ):
        return None
    # Leave multi-edit commands and genuine requests for a definition to the model.
    if len(re.findall(r"改成|替换成|换成", u)) > 1 or re.search(r"解释一下|解释.{1,12}意思|两边", u):
        return None
    wordslots = []
    for m in re.finditer("(" + HAN + "{1,9}?)的(" + HAN + ")", u):
        word, heard = m.groups()
        word = re.split(r"改成|换成|是|那个|就是|应该用", word)[-1] or word
        if heard in "那这第时候":
            continue
        if heard in word:
            picked = heard
        else:
            options = {c for c in word if reading(c) == reading(heard)}
            if len(options) != 1:
                return None
            picked = options.pop()
        tail = re.match(HAN + "{1,3}", u[m.end() :])
        if tail and word.endswith(heard + tail.group()):
            picked = heard + tail.group()
        wordslots.append(picked)
    if not wordslots:
        return None
    if len(wordslots) == 1 and not re.search("是|改成|解释|换成|" + HAN + "的" + HAN + "$", u):
        return None
    spelled = "".join(wordslots)
    prefix = re.match("^(?:把)?(" + HAN + "{1,8}?)(?:改成|换成|是)", u)
    named = prefix.group(1) if prefix else ""
    # If one slot of a longer word was described, only select a single old character.
    if (
        named
        and len(spelled) == 1
        and len(named) > 1
        and not (named.endswith(spelled) or named in text)
    ):
        return None
    if named and len(spelled) == 1 and len(named) > 1:
        named = ""
    if re.search(r"不是", u) and not re.search(r"是不是", u):
        return None
    target = target_span(text, named, spelled)
    if not target or target == spelled:
        return None
    return place(text, target, spelled)


def literal_result(inp):
    text = inp["editable_text"]
    u = phrase(inp["utterance"])
    if not text or not u:
        return None
    # Quoted replacement strings and compound commands are delegated intact.
    if EXTRA.search(u) or QUALIFIER.search(u):
        return None
    m = re.fullmatch(
        r"(?:在|把|给)?(.+?)(?:的)?(前面|后面|之前|之后)(?:再)?(?:加上|加个|加|插入)(.+)", u
    )
    if m and not re.search(r"[，。！？；]", u):
        anchor, side, value = m.groups()
        value = spoken_symbol(phrase(value))
        if text.count(anchor) == 1 and value and not re.search(r"的[一-龥]$", value):
            return place(text, anchor, value + anchor if side in ("前面", "之前") else anchor + value)
    m = re.fullmatch(r"(?:把)?(.{1,25}?)(?:改成为|改成|换为|替换成|换成)(.{1,25})", u)
    if m and not re.search(r"[的，。！？；]|不是|大写|小写|前面|后面|先|再|然后|开头|最后", u):
        old, new = (spoken_symbol(phrase(x)) for x in m.groups())
        if old in text:
            return place(text, old, new)
    m = re.fullmatch(
        r"(?:(?:把)?(前面的|后面的|最后的)?(.{1,6}?)(?:去掉|删掉)|(?:去掉|删除|不要)(前面的|后面的|最后的)?(.{1,6}))",
        u,
    )
    if m:
        pos = m.group(1) or m.group(3) or ""
        target = m.group(2) or m.group(4)
        sym = SYMBOLS.get(target)
        if not sym and target not in text:
            plausible = {v for k, v in SYMBOLS.items() if v in text and sound(k) == sound(target)}
            if len(plausible) == 1:
                sym = plausible.pop()
        if sym and sym in text:
            if pos == "前面的":
                return text.replace(sym, "", 1)
            if pos:
                return "".join(text.rsplit(sym, 1))
            return text.replace(sym, "")
    return parse_glyph(u, text)
