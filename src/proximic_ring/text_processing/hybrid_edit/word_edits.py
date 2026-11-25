"""Literal changes, phonetic word candidates and task-specific input selection."""

import re
from collections import defaultdict
from functools import lru_cache
from importlib.resources import files

from .literal import phrase, sound, spoken_symbol, literal_result
from .phonetics import reading, tonal
from .vocabulary import current_vocabulary

HAN = r"[\u3400-\u9fff]"
PUNCT = "，。！？；、,.!?；;"
CN = {
    "一": 1,
    "两": 2,
    "二": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
}


def number(s):
    return int(s) if s.isdecimal() else CN.get(s)


def separated(u):
    return re.search(
        "然后|接着|再把|并且|同时|另外|翻译|分行|不要动|保留.{1,7}不|前面的|后面的|第[一二三四五六七八九十0-9]",
        u,
    )


@lru_cache(maxsize=1)
def lexicon():
    buckets = defaultdict(list)
    freq = {}
    file = files("jieba").joinpath("dict.txt")
    with file.open(encoding="utf-8") as stream:
        for line in stream:
            word, n, *_ = line.split()
            if 2 <= len(word) <= 4 and re.fullmatch(HAN + "+", word):
                freq[word] = int(n)
                buckets[sound(word)].append(word)
    for bucket in buckets.values():
        bucket.sort(key=lambda w: -freq[w])
    return freq, buckets


def literal_slot(text, word):
    # For a misrecognised entire word, require a unique compatible location.
    if not word:
        return None
    if word in text:
        return None
    n = len(word)
    candidates = {
        text[i : i + n] for i in range(len(text) - n + 1) if re.fullmatch(HAN + "+", text[i : i + n])
    }
    exact = [s for s in candidates if sound(s) == sound(word)]
    if len(exact) == 1:
        return exact[0]
    if exact or n < 2:
        return None

    def compatible(a, b):
        if a == b:
            return True
        for ends in [("ong", "eng"), ("in", "ing"), ("en", "eng"), ("an", "ang")]:
            for x, y in (ends, ends[::-1]):
                if a.endswith(x) and b == a[: -len(x)] + y:
                    return True
        return False

    near = [
        s
        for s in candidates
        if any(x == y for x, y in zip(s, word))
        and all(compatible(a, b) for a, b in zip(sound(s), sound(word)))
    ]
    return near[0] if len(near) == 1 else None


def pairs(u):
    out = []
    for m in re.finditer("(" + HAN + "{1,12}?)的(" + HAN + ")", u):
        raw, heard = m.groups()
        if heard in "这那个":
            continue
        raw = re.split("改成|换成|那个|就是|是|应该用", raw)[-1] or raw
        # A conventional spelling word can itself have been transcribed phonetically.
        vocab = current_vocabulary()
        if len(raw) >= 2 and raw not in vocab.words and not lexicon()[0].get(raw):
            words = lexicon()[1].get(sound(raw), [])
            # A competing personal spelling makes automatic inference unsafe.
            # Keep the spoken clue literal and offer homophones in the model's term task.
            if any(w not in words for w in vocab.by_sound.get(sound(raw), ())):
                words = []
            toned = [w for w in words if tonal(w) == tonal(raw)]
            if len(toned) == 1:
                raw = toned[0]
            elif len(words) == 1:
                raw = words[0]
        chars = [ch for ch in raw if reading(ch) == reading(heard)]
        if not chars:
            return []
        picked = heard if heard in raw else chars[-1]
        tail = re.match(HAN + "{1,3}", u[m.end() :])
        if tail and raw.endswith(picked + tail.group()):
            picked += tail.group()
        out.append(picked)
    return out


def plain_replace(text, old, new):
    if old not in text or not old or not new:
        return None
    return text.replace(old, new)


def local(inp):
    utterance = phrase(inp["utterance"])
    if re.search(
        r"只(?:删|替换|动|在|保留)|其[余他]|除.{1,12}以外|不要(?:删|动)|别(?:删|动)|引号|不包括|保留.{1,10}原样|不要翻译",
        utterance,
    ):
        return None
    if "的" in utterance and separated(utterance) and ("然后" in utterance or "并且" in utterance):
        return None
    t = inp["editable_text"]
    u = phrase(inp["utterance"])
    if not t or not u:
        return None
    # Scope, quotations, translation and other secondary requests require the full oral instruction.
    if re.search(
        r"第[二三四五六七八九十0-9]|只改|只在|只把|引号|翻译|译成|翻成|分行|接着|[“”「」]|里面的|后面的.{0,5}字|前面的.{0,5}字",
        u,
    ):
        return None
    if re.search("不要把|别把|不要改|先别|不用改|如果|引号里|引用|台词|不改|原样保留|[“”「」]", u):
        return None
    if re.fullmatch(r"(?:OK|好|嗯|好的)?[，, ]*(?:删除|删掉|去掉)语气词", u, flags=re.I):
        v = re.sub(r"[吧呢呀啊啦嘛哇哦呗]+(?=[，。！？!?]|$)", "", t)
        if v != t:
            return "语气词删除", v
    m = re.fullmatch(
        r"(?:请|帮我)?(?:删除|删掉|去掉)(?:正文)?(?:的)?(最[前后]|开头|末尾|结尾)([0-9一两二三四五六七八九十]+)个(?:字|字符)",
        u,
    )
    if m:
        n = number(m[2])
        v = t.rstrip()
        if n and n <= len(v):
            return ("字符计数", v[n:] if m[1] in ["最前", "开头"] else v[:-n])
    m = re.fullmatch(r"(?:请|帮我)?(?:删除|删掉|去掉)(?:正文)?(最后一|第一|最前一)句(?:话)?", u)
    if m:
        spans = list(re.finditer(r"[^。！？!?]+(?:[。！？!?]+[”’」』]*)?|[。！？!?]+", t.rstrip()))
        if spans:
            if m[1] == "最后一":
                return "句子范围", t[: spans[-1].start()].rstrip()
            return "句子范围", t[spans[0].end() :].lstrip()
    if re.fullmatch(r"(?:在)?(?:全文)?(?:末尾|最后)(?:的)?(" + HAN + ")[，, ]*(?:去掉|删掉|删除)", u):
        ch = re.search(r"(?:末尾|最后)(?:的)?(" + HAN + ")", u)[1]
        m = re.search(re.escape(ch) + "([" + PUNCT + r"\s]*)$", t)
        if m:
            return "末尾指定字", t[: m.start()] + m[1]
    if re.fullmatch(r"(?:把)?[^，。！？,!?]+(?:和|以及)[^，。！？,!?]+(?:去掉|删掉)", u):
        m = re.fullmatch(r"(?:把)?(.+?)(?:以及|和)(.+?)(?:去掉|删掉)", u)
        words = [spoken_symbol(v) for v in m.groups()]
        if all(v in t for v in words):
            return "并列删除", re.sub(
                "|".join(re.escape(x) for x in sorted(words, key=len, reverse=True)), "", t
            )
    m = re.fullmatch(
        r"(?:把)?("
        + HAN
        + "{1,12}?)(?:改成|变成|替换成|换成)("
        + HAN
        + "{1,12})[，, ]*("
        + HAN
        + "{1,12})不是("
        + HAN
        + "{1,8})",
        u,
    )
    if m and m[4] in m[2] and m[3].startswith(m[4]) and len(m[3]) > len(m[4]):
        new = m[2].replace(m[4], m[3][len(m[4]) :])
        for q in ("很多", "一些", "几种", "各个", "许多"):
            if new.startswith(q) and q + m[1] in t:
                return "改口与量词", t.replace(q + m[1], new)
        v = plain_replace(t, m[1], new)
        if v is not None:
            return "最终改口", v
    m = re.fullmatch(
        r"(?:都改成[^，,]+[，,])?(?:把)?("
        + HAN
        + "{1,12}?)(?:都)?(?:改成|变成|替换成|换成)("
        + HAN
        + "{1,15})",
        u,
    )
    if m and not separated(u) and not re.search(r"的|不是|解释|大写|小写|语气|正式|礼貌|委婉", m[2]):
        v = plain_replace(t, *m.groups())
        if v is not None:
            return "字面替换", v
    m = re.fullmatch(r"(" + HAN + "{1,10})不是(" + HAN + "{1,10})", u)
    if m and not separated(u) and m[2] in t:
        return "正反纠正", t.replace(m[2], m[1])
    if re.search("三点水|单人旁|双人旁|草字头|提手旁|木字旁|口字旁|竖心旁|言字旁|金字旁|偏旁|部首", u):
        return None
    if "的" in u and not separated(u) and not re.search(r"什么意思|解释一下|[引“”「」]", u):
        cp = pairs(u)
        if cp:
            word = "".join(cp)
            named = re.match("^(?:把)?(" + HAN + "{1,8}?)(?:改成|换成|是)", u)
            if named and len(named[1]) == len(word) and named[1] in t:
                return "逐字线索", t.replace(named[1], word)
            slot = literal_slot(t, word)
            if slot:
                return "同音字位", t.replace(slot, word)
    v = literal_result(inp)
    if v is not None:
        return "字词或符号定位", v
    return None


def spelled_span(text, utterance):
    clauses = re.split(r"[，、,]", utterance)
    if not 3 <= len(clauses) <= 8:
        return None
    matches = [re.fullmatch("(" + HAN + "{1,8})的(" + HAN + ")", c.strip()) for c in clauses]
    if not all(matches):
        return None
    syllables = sound("".join(m[2] for m in matches))
    size = len(syllables)
    slots = {}
    for start in range(len(text) - size + 1):
        candidate = text[start : start + size]
        if re.fullmatch(HAN + "+", candidate):
            distance = sum(a != b for a, b in zip(syllables, sound(candidate)))
            if distance <= 1:
                slots[candidate] = distance
    if not slots:
        return None
    closest = [slot for slot in slots if slots[slot] == min(slots.values())]
    if len(closest) == 1:
        return closest[0], "依次解释每个字：" + " / ".join(clauses)
    return None


def semantic_mode(inp):
    if spelled_span(inp["editable_text"], phrase(inp["utterance"])):
        return "term"
    if re.search(
        r"(复现|观察|排查|诊断|发现|验证|确认)(?:了|到|出)?(?:什么|哪些|哪种|哪个)", inp["utterance"]
    ):
        return "facts"
    if re.fullmatch(
        r"(?:说什么回复[？?])?(?:请|帮我)?(?:拟(?:一个)?回复|回(?:复)?)(?:一下)?(?:对方|老师)?",
        phrase(inp["utterance"]),
    ):
        return "reply"
    u = phrase(inp["utterance"])
    t = inp["editable_text"]
    if (
        t
        and re.search(r"偏旁|三点水|[字木口言金衣手心人]旁|的意思|是.{1,8}的那个", u)
        and not separated(u)
    ):
        return "term"
    if re.search(
        r"回复|帮我回|复现了什么|总结.*消息|刚才.*(摘要|讲|说)|写.{0,18}(邮件|文章|故事|报告)|用.*字.*写",
        u,
    ):
        return "write"
    if re.search(r"润色|优化.*语言|整理|短话|委婉|正式|简短", u):
        return "style"
    return "edit"


def term_task(t, u):
    span = spelled_span(t, u)
    if span:
        return span
    m = re.search(r"(?:是|就是).{1,12}的那个(" + HAN + "{1,8})$", u)
    if m and m[1] in t:
        old = m[1]
    else:
        old = next((u[:n] for n in range(min(8, len(u)), 0, -1) if u[:n] in t), "")
    if not old:
        return None
    _, buckets = lexicon()
    vocab = current_vocabulary()
    keys = sound(old)
    # Offer ordinary lexical candidates for homophones and common Mandarin onset confusions.
    keys_to_try = [keys]
    for i, v in enumerate(keys):
        for a, b in [("l", "n"), ("r", "l"), ("zh", "z"), ("ch", "c"), ("sh", "s")]:
            for x, y in ((a, b), (b, a)):
                if v.startswith(x):
                    keys_to_try.append((*keys[:i], y + v[len(x) :], *keys[i + 1 :]))
    words = list(dict.fromkeys(w for key in keys_to_try for w in vocab.candidates(key, buckets)[:28]))
    hint = "、".join(words)
    cue = re.search("那个(" + HAN + "{2,5})的", u)
    if cue:
        hint += "；解释词可能的识别候选：" + "、".join(vocab.candidates(sound(cue[1]), buckets)[:15])
    return old, hint


def finalize(inp, out):
    u = phrase(inp["utterance"])
    m = re.search(r"[，,](?:然后|并且)([^，。！？、,!?]{1,8})(?:去掉|删掉)$", u)
    if m and m[1] in inp["editable_text"]:
        out = out.replace(m[1], "")
    gap = re.search(r"(?:中间|之间)空([0-9一二三四五六七八九十]+)行", u)
    if gap and number(gap[1]) is not None and number(gap[1]) <= 100:
        lines = [l for l in out.splitlines() if l.strip()]
        if len(lines) == 2:
            out = ("\n" * (number(gap[1]) + 1)).join(lines)
    return out


def user_message(mode, inp):
    if mode == "facts":
        return {
            "提问": inp["utterance"],
            "草稿": inp["editable_text"],
            "给定资料": inp["screen_context"],
            "保存资料": inp.get("saved_context", ""),
        }
    if (
        mode == "write"
        and not inp["editable_text"].strip()
        and re.fullmatch(
            r"(?:请|帮我|麻烦)?(?:写|拟|起草)(?:一个|个|一封|封|篇)?(?:[0-9一二三四五六七八九十百千]+字(?:左右)?的?)?(?:邮件|信|文章)",
            phrase(inp["utterance"]),
        )
    ):
        return {
            "写作任务": inp["utterance"],
            "原有草稿": "",
            "备注": "自由主题。写一份独立、通用、可直接使用的成品。",
        }
    if mode == "reply":
        ctx = inp["screen_context"]
        chat = ctx.get("context") or ctx.get("messages") or ctx
        return {
            "委托": inp["utterance"],
            "对话": chat,
            "原有草稿（仅在切题时使用）": inp["editable_text"],
        }
    if mode == "style":
        return {"编辑要求（不写入正文）": inp["utterance"], "原稿（仅改写此处）": inp["editable_text"]}
    if mode == "write":
        u = inp["utterance"]
        m = {"写作任务": u, "原有草稿": inp["editable_text"]}
        if not (
            re.search("写.{0,16}(文章|小文章|故事)", u)
            and not re.search("上文|屏幕|保存|刚才|资料|据此", u)
        ):
            m["当时可见的上文"] = inp["screen_context"]
            if "saved_context" in inp:
                m["之前保存的资料"] = inp["saved_context"]
        return m
    return inp


def surface_length(text):
    return len(re.findall(HAN + "|[A-Za-z0-9]+", text))


def feedback(inp, out, mode):
    if mode == "reply" and (
        not out.strip() or out.strip() in (inp["editable_text"].strip(), inp["utterance"].strip())
    ):
        return "请结合对方刚说过的一件事，写一句实际回复。"
    u = inp["utterance"]
    t = inp["editable_text"]
    if mode == "write":
        match = re.search(r"([0-9]+)\s*个?字", u)
        if match and int(match[1]) >= 30:
            n = int(match[1])
            actual = surface_length(out)
            if actual < n * 0.76 or actual > n * 1.4:
                return (
                    f"刚才正文长度约为 {actual}，请重写到要求的约 {n} 字，可展开为多段，交付完整成品。"
                )
    if mode == "style":
        if re.search("精简|短|简练", u) and len(out.strip()) >= len(t.strip()) and len(t.strip()) >= 8:
            return "你返回的成品没有缩短。保留原文数字、独立动作和请求，明显缩短句子。"
        if any(w in out and w not in t and w in u for w in ("润色", "委婉一点", "正式点", "整理一下")):
            return "把指挥编辑器的话混进了成品，请仅改写原稿，省去描述编辑动作的文字。"
    return ""


def _apply_exact_term(text, old, out):
    # A model can return the corrected full draft in place of the lexical answer.
    if len(out) == len(old) and out.strip() == out and re.fullmatch(HAN + "+", out):
        return text.replace(old, out)
    if len(out) == len(text) and old in text:
        at = text.index(old)
        candidate = out[at : at + len(old)]
        if re.fullmatch(HAN + "+", candidate) and text.replace(old, candidate) == out:
            return out
    return text


def apply_term(text, old, raw):
    safe = _apply_exact_term(text, old, raw)
    if safe != text or len(text) != len(raw) or text.count(old) != 1:
        return safe
    # A full draft with a stable neighbouring anchor can supply a same-sounding glyph.
    start = text.index(old)
    end = start + len(old)
    candidate = raw[start:end]
    if len(old) != 1 or not re.fullmatch(HAN, candidate) or sound(candidate) != sound(old):
        return safe
    anchored = any(
        len(a) >= 2 and a == b and re.fullmatch(HAN + "+", a)
        for a, b in (
            (text[max(0, start - 2) : start], raw[max(0, start - 2) : start]),
            (text[end : end + 2], raw[end : end + 2]),
        )
    )
    return text[:start] + candidate + text[end:] if anchored else safe
