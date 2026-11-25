"""Edit spans, protected spellings and full-text task selection."""

from dataclasses import dataclass
import json
import re
from . import word_edits
from .literal import SYMBOLS
from .prompts import prompt

HAN = word_edits.HAN
WORDS = r"[\u3400-\u9fffA-Za-z0-9_-]+"
LIMIT = re.compile(r"如果|除非|假如|不要(?:把|改|删)|别(?:把|改|删)|引号|原样|不用改|先别|仅限")
SUFFIX = re.compile(
    r"[，,。；;]\s*(?:(?:然后|接着|之后|整体|最后|再|并且|并)\s*)*(?P<task>(?:润色|分(?:成)?[两二三四五六七八九十0-9]*行|翻译|译成|翻成|说得|说的|改得|改写|语气|用.{1,6}语).*)$"
)


def clean(s):
    return s.strip().rstrip("。！？!?,，；;").strip()


@dataclass(frozen=True)
class Change:
    old: str
    new: str
    spans: tuple[tuple[int, int], ...]

    def apply(self, source):
        parts = []
        end = 0
        for start, stop in self.spans:
            if start < end or source[start:stop] != self.old:
                raise ValueError("替换位置与原文不符")
            parts.extend([source[end:start], self.new])
            end = stop
        return "".join(parts) + source[end:]


def change(t, old, new, which="all"):
    if not old or not new or old == new:
        return None
    spans = tuple(m.span() for m in re.finditer(re.escape(old), t))
    if not spans:
        return None
    if which == "first":
        spans = spans[:1]
    if which == "last":
        spans = spans[-1:]
    return Change(old, new, spans)


def lexical(text, u):
    scope = re.fullmatch(
        r"(?:请)?(?:只)?(?:把)?(第一个|前一个|前面那个|第二个|后一个|后面那个|最后一个)("
        + WORDS
        + r")(?:换成|改成|替换成)("
        + WORDS
        + r")(?:[，,](?:前面|后面)的(?:别动|不动))?",
        u,
    )
    if scope:
        order, old, new = scope.groups()
        if order == "第二个":
            op = change(text, old, new)
            return Change(old, new, op.spans[1:2]) if op and len(op.spans) >= 2 else None
        return change(text, old, new, "first" if order in ("第一个", "前一个", "前面那个") else "last")
    m = re.fullmatch(
        r"(?:请)?(?:只)?(?:把)?("
        + WORDS
        + r")(?:(?:改|变)成为|替换成|换成|改成|改为|变成)("
        + WORDS
        + r")",
        u,
    )
    if m and not re.search(r"的|先|润色|翻译|重新|再|前面|后面|最后|第|只", m[1] + m[2]):
        old, new = m.groups()
        if ("改成为" in u or "变成为" in u) and new.startswith(
            ("了", "何", "主", "辅", "准", "首", "例", "由", "名", "期", "止")
        ):
            # “改成为了” can denote the replacement text 为了. Full interpretation is semantic.
            return None
        return change(text, old, new)
    # A pause immediately before the selected character may be ASR punctuation.
    normalized = re.sub(r"的[，,]\s*(" + HAN + r")$", r"的\1", u)
    if (
        "的" not in normalized
        or LIMIT.search(normalized)
        or re.search("第|前面|后面|不是|动手|解释一下|意思", normalized)
    ):
        return None
    if not re.search(r"的" + HAN + r"(?:$|[，,。]|" + HAN + r"是)", normalized):
        return None
    parts = word_edits.pairs(normalized)
    if not parts:
        return None
    word = "".join(parts)
    # Prefer the literal name spoken before “改成/是”. Only accept its full length.
    m = re.match(r"^(?:把)?(" + HAN + r"{1,12}?)(?:改成|换成|是)", normalized)
    if m and len(m[1]) == len(word) and m[1] in text:
        return change(text, m[1], word)
    if m and len(m[1]) > len(word) and m[1] in text:
        old = m[1][-len(word) :]
        if word_edits.sound(old) == word_edits.sound(word):
            return change(text, old, word)
    slot = word_edits.literal_slot(text, word)
    if slot:
        return change(text, slot, word)
    # Explicitly named old sound can differ from the sound of the new glyph.
    if m and len(m[1]) == len(word) and (slot := word_edits.literal_slot(text, m[1])):
        return change(text, slot, word)
    return None


def mark_candidates(text, op, allow_alias):
    # Confirmed matches are locked. A differently written homophone remains optional.
    # Distinct markers also allow a downstream check that order and scope survived.
    fixed = set(op.spans)
    upcoming = iter(op.spans)
    next_fixed = next(upcoming, None)
    pronunciation = word_edits.sound(op.old)
    width = len(op.old)
    slots = []
    for i in range(len(text) - width + 1):
        span = (i, i + width)
        if slots and i < slots[-1][1]:
            continue
        fragment = text[i : i + width]
        while next_fixed is not None and next_fixed[1] <= i:
            next_fixed = next(upcoming, None)
        disjoint = next_fixed is None or i + width <= next_fixed[0]
        sounds_alike = re.fullmatch(HAN + "+", fragment) and word_edits.sound(fragment) == pronunciation
        optional = width >= 2 and fragment != op.old and sounds_alike
        protected = not allow_alias and (fragment == op.old or sounds_alike)
        if span in fixed or (disjoint and (optional or protected)):
            slots.append((i, i + width, span in fixed))
    markers = []
    parts = []
    offset = 0
    for j, (a, b, locked) in enumerate(slots, 1):
        original = text[a:b]
        value = op.new if locked else original
        markers.append(
            dict(
                id=j,
                original=original,
                value=value,
                allowed=[value] if locked or not allow_alias else list(dict.fromkeys([value, op.new])),
            )
        )
        parts.extend([text[offset:a], f"⟦T{j}⟧{value}⟦/T{j}⟧"])
        offset = b
    return "".join(parts) + text[offset:], markers


def local_or_polish(inp):
    text = inp["editable_text"]
    u = clean(inp["utterance"])
    if not text or not u:
        return None
    if re.fullmatch(
        r"(?:请|帮我)?(?:删除|删掉|清除|清空)(?:这句话|当前这句话|这一段|这段话|编辑框|全部内容|全部|全文)?",
        u,
    ):
        return "清空当前输入", "", None
    if re.fullmatch(r"(?:请|帮我)?(?:删除|删掉|去掉)(?:上(?:面)?一|上一|最后一)句(?:话)?", u):
        spans = list(
            re.finditer(r"[^。！？!?]+(?:[。！？!?]+[”’」』]*[。！？!?]*)?|[。！？!?]+", text.rstrip())
        )
        if spans:
            return "从末尾删除一句", text[: spans[-1].start()].rstrip(), None
    symbol = re.fullmatch(
        r"(?:请|帮我)?(?:把)?(最后|末尾|第[一二三四五六七八九十0-9]+|最前)(?:个|一个|一处|处)?(句号|问号|逗号|叹号|感叹号|冒号|分号|顿号)(?:去掉|删掉|删除)(?:[，,](?:其余|其他)(?:都)?(?:保留|不变|不动))?",
        u,
    )
    prefix = re.fullmatch(
        r"(?:请|帮我)?(?:删除|去掉|删掉)(第[一二三四五六七八九十0-9]+|最后|最前)(?:个|一个|一处|处)?(句号|问号|逗号|叹号|感叹号|冒号|分号|顿号)(?:[，,](?:其余|其他)(?:都)?(?:保留|不变|不动))?",
        u,
    )
    if symbol or prefix:
        which, word = (symbol or prefix).groups()
        ch = SYMBOLS[word]
        equivalents = {"？": "？?", "！": "！!", "，": "，,", "：": "：:", "；": "；;"}
        # Only treat Chinese full stops as sentence punctuation. An ASCII dot can be part of a number or URL.
        hits = [i for i, c in enumerate(text) if c in equivalents.get(ch, ch)]
        index = (
            len(hits)
            if which in ("最后", "末尾")
            else 1
            if which == "最前"
            else word_edits.number(which[1:])
        )
        if index and 1 <= index <= len(hits):
            at = hits[index - 1]
            return "指定次序的标点", text[:at] + text[at + 1 :], None
    if LIMIT.search(u):
        return None
    anchor = re.fullmatch(
        r"(?:在)?(.{1,20}?)(?:的)?(前面|后面|之前|之后)(?:再)?(?:加上|加个|加|插入)(.{1,20})", u
    )
    if anchor and text.count(anchor[1]) == 1 and not re.search("[，,。；;]", u):
        a, side, v = anchor.groups()
        m = re.fullmatch("(" + HAN + "{1,9})的(" + HAN + ")", v)
        if m:
            vlist = {c for c in m[1] if word_edits.reading(c) == word_edits.reading(m[2])}
            if len(vlist) != 1:
                return None
            v = vlist.pop()
        elif not re.fullmatch(WORDS, v) or "再" in v:
            return None
        v = word_edits.spoken_symbol(v)
        return (
            "紧邻原文锚词插入",
            text.replace(a, v + a if side in ("前面", "之前") else a + v, 1),
            None,
        )
    tail = SUFFIX.search(u)
    if tail and "⟦" not in text and "⟧" not in text:
        head = clean(u[: tail.start()])
        op = lexical(text, head)
        if op:
            allow_alias = "的" in head and not re.search("只|第|仅|前一个|后一个|最后一个", head)
            marked, markers = mark_candidates(text, op, allow_alias)
            return (
                "先替换再精修",
                op.apply(text),
                dict(
                    tail=tail["task"],
                    head=head,
                    old=op.old,
                    new=op.new,
                    spans=op.spans,
                    marked=marked,
                    markers=markers,
                ),
            )
    if not tail and not re.search("然后|另外|分行|翻译|说得|润色|[“”「」]", u):
        op = lexical(text, u)
        if op:
            return "原位替换", op.apply(text), None
    return None


def plan(inp):
    t = inp["editable_text"]
    u = clean(inp["utterance"])
    result = local_or_polish(inp)
    if result:
        method, draft, patch = result
        if patch:
            translated = bool(re.search(r"译|翻成|用.{1,5}语", patch["tail"]))
            body = {
                "已改好的原稿": draft if translated else patch["marked"],
                "剩余委托": patch["tail"],
                "完整口述": inp["utterance"],
                "已确定写法": patch["new"],
                "屏幕资料": inp["screen_context"],
                "保存资料": inp.get("saved_context", ""),
            }
            if not translated:
                body["各位置允许的写法"] = [
                    {"编号": m["id"], "写法": m["allowed"]} for m in patch["markers"]
                ]
            return dict(
                method=method,
                mode="translate" if translated else "polish",
                draft=draft,
                patch=patch,
                user=body,
            )
        return dict(method=method, mode="local", draft=draft)
    # Preserve complete scope / clause meaning if the exact parser abstained.
    restricted = bool(
        LIMIT.search(u)
        or re.search(r"(?:第[二三四五六七八九十0-9]|前一个|后一个|只把|只改|前面那个|后面那个)", u)
        or "改成为" in u
        or "变成为" in u
    )
    literal = None if restricted else word_edits.local(inp)
    if literal:
        return dict(method=literal[0], mode="local", draft=literal[1])
    mode = word_edits.semantic_mode(inp)
    patch = None
    if re.search(r"(?:只|就)(?:留|保留)一[句行]|改成.{0,4}故事的?情节", u):
        mode = "condense" if "留" in u else "compose"
    elif re.search(r"写.{0,18}(?:文章|故事|短文|小文|报告)", u) and not re.search(r"续写|接着写", u):
        mode = "compose"
    elif mode == "style" and re.search(r"上文|屏幕|OCR|资料|截图", u, re.I):
        mode = "context_style"
    elif (
        mode == "style"
        and re.search(r"改成|改为|换成|去掉|删掉", u)
        and re.search(r"然后|再|接着|[，,].*[，,]", u)
    ):
        mode = "compound"
    if mode in ("compose", "condense", "context_style", "compound"):
        body = {"本次委托": inp["utterance"], "编辑框原文": t}
        if mode != "compose" or re.search("上文|屏幕|资料|根据|摘要|刚才", u):
            body.update(屏幕资料=inp["screen_context"], 保存资料=inp.get("saved_context", ""))
    else:
        body = word_edits.user_message(mode, inp)
        if mode == "reply" and inp["screen_context"].get("conversation_title"):
            body["当前对话名"] = inp["screen_context"]["conversation_title"]
        if mode == "reply" and inp["screen_context"].get("topic"):
            body["当前对话主题及参与者"] = inp["screen_context"]["topic"]
        if mode == "term":
            patch = word_edits.term_task(t, word_edits.phrase(u))
            if patch:
                body = dict(
                    original=t,
                    utterance=inp["utterance"],
                    old_word=patch[0],
                    phonetic_candidates=patch[1],
                )
            else:
                mode = "edit"
                body = inp
    return dict(method=mode, mode=mode, patch=patch, user=body)


def finish(p, inp, out):
    if p["mode"] == "polish":
        markers = p["patch"]["markers"]
        pattern = r"⟦T(\d+)⟧([^⟦⟧]*)⟦/T\1⟧"
        found = list(re.finditer(pattern, out))
        if len(found) == len(markers) and all(
            int(m[1]) == slot["id"] and m[2] in slot["allowed"] for m, slot in zip(found, markers)
        ):
            final = re.sub(pattern, lambda m: m[2], out)
            if "⟦" not in final and "⟧" not in final:
                return final
        return None
    if p["mode"] == "term" and p.get("patch"):
        out = word_edits.apply_term(inp["editable_text"], p["patch"][0], out)
    if p["mode"] in ("translate", "context_style", "compose", "condense", "compound"):
        return out
    return word_edits.finalize(inp, out)


def run_task(inp, plan, complete):
    mode = plan["mode"]
    if mode == "local":
        return plan, plan["draft"], "规则直接交付"
    messages = [
        {"role": "system", "content": prompt(mode)},
        {"role": "user", "content": json.dumps(plan["user"], ensure_ascii=False)},
    ]
    raw = complete(messages, None)
    out = json.loads(raw)["modified_text"]
    correction = word_edits.feedback(inp, out, mode)
    final = finish(plan, inp, out)
    if final is None:
        correction = (
            "初稿中的保护标记必须原样在最终正文各出现一次；请在已改好的原稿上落实剩余委托，交付全文。"
        )
    if correction:
        messages.extend(
            [{"role": "assistant", "content": raw}, {"role": "user", "content": correction}]
        )
        final = finish(plan, inp, json.loads(complete(messages, None))["modified_text"])
    return plan, plan["draft"] if final is None else final, mode
