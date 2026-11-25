"""Locate a spoken replacement and any following instructions in the editable source."""

import re
from . import tasks, word_edits
from .vocabulary import current_vocabulary

HAN = tasks.HAN
QUOTES = {"“": "”", "「": "」", "『": "』", "‘": "’", '"': '"'}
PAUSES = set("，,。；;、？！!?")
SCOPE = r"第一个|第二个|最前一个|前一个|前面那个|后一个|后面那个|最后一个"
VERB = r"替换成|换成|改成|改为|变成"
CONDITION = re.compile(
    r"如果|假如|除非|算了|撤回|先别|别改|不改了|只(?:动|改|换|限)|仅(?:动|改|在)|原样保留"
)
CONNECT = re.compile(r"然后|接着|之后|以后|并且|同时|另外|整体再|再(?=用|把|说|分|译|翻)")
EXPLICIT = re.compile(
    r"^(?:请)?(?:只)?(?:把)?(?P<scope>"
    + SCOPE
    + r")?(?P<old>"
    + HAN
    + r"{0,16}?)("
    + VERB
    + r")(?P<body>.+)$"
)
GLYPH = re.compile(r"的" + HAN)
SCHEMA = {
    "type": "json_schema",
    "json_schema": {
        "name": "review_edit",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "decision": {"type": "string", "enum": ["keep", "edit", "translate"]},
                "modified_text": {"type": "string"},
            },
            "required": ["decision", "modified_text"],
            "additionalProperties": False,
        },
    },
}


def spoken(s):
    out = []
    closing = None
    # A pause or space inside a literal product name is part of its spelling.
    in_name = {i for m in current_vocabulary().name_matches(s) for i in range(*m.span())}
    for i, char in enumerate(s):
        if closing:
            out.append(char)
            if char == closing:
                closing = None
        elif char in QUOTES:
            closing = QUOTES[char]
            out.append(char)
        elif i in in_name or (char not in PAUSES and not char.isspace()):
            out.append(char)
    return "".join(out)


def scope_change(text, old, new, scope):
    op = tasks.change(text, old, new)
    if op is None or not scope:
        return op
    i = 1 if scope == "第二个" else -1 if scope in ("后一个", "后面那个", "最后一个") else 0
    return tasks.Change(old, new, (op.spans[i],)) if i < len(op.spans) else None


def implicit_anchor(text, new):
    if len(new) < 2 or not re.fullmatch(HAN + "+", new):
        return None
    options = {
        w
        for w in current_vocabulary().cut(text)
        if len(w) == len(new)
        and w != new
        and re.fullmatch(HAN + "+", w)
        and sum(a != b for a, b in zip(w, new)) == 1
    }
    if len(options) == 1:
        old = options.pop()
        if text.count(old) == 1:
            return old
    return None


def glyph(text, u):
    # A repeated locator as in “某词的某，是……” is not an additional spelling clue.
    clause = re.sub(r"的(" + HAN + r")(?=是)", r"\1", u)
    scope = re.match("^(" + SCOPE + ")(" + HAN + r")\2(?:是|就是)", clause)
    if scope:
        clues = word_edits.pairs(clause[scope.end() :])
        if len(clues) != 1 or len(clues[0]) != 1:
            return None
        matches = [
            (i, c)
            for i, c in enumerate(text)
            if re.fullmatch(HAN, c) and word_edits.sound(c) == word_edits.sound(scope[2])
        ]
        index = (
            1 if scope[1] == "第二个" else -1 if scope[1] in ("后一个", "后面那个", "最后一个") else 0
        )
        if matches and index < len(matches):
            i, old = matches[index]
            return tasks.Change(old, clues[0], ((i, i + 1),)) if old != clues[0] else None
        return None
    return tasks.lexical(text, clause)


def parse(inp):
    text, u = inp["editable_text"], spoken(inp["utterance"])
    if not u or not text or "⟦" in text or "⟧" in text:
        return None
    # A quoted operand may contain verb-like words. Ignore its contents for scope checks.
    visible = re.sub(r'“[^”]*”|「[^」]*」|『[^』]*』|‘[^’]*’|"[^"]*"', "□", u)
    if CONDITION.search(visible):
        return None
    m = EXPLICIT.fullmatch(u)
    glyph_ends = list(GLYPH.finditer(u))
    if glyph_ends and not any(q in u for q in QUOTES):
        # Only accept valid spelling clauses. The next clause is not consumed as a command word.
        for match in reversed(glyph_ends):
            head, tail = u[: match.end()], u[match.end() :]
            op = glyph(text, head)
            if op:
                return op, head, tail, not bool(re.search(SCOPE, head))
    if not m:
        return None
    body, old, scope = m["body"], m["old"], m["scope"]
    if old and old not in text:
        return None
    if body[0] in QUOTES:
        end = body.find(QUOTES[body[0]], 1)
        if end < 2:
            return None
        new, tail = body[1:end], body[end + 1 :]
    else:
        if any(ch in body for ch in QUOTES):
            return None
        link = CONNECT.search(body)
        if link and link.start():
            new, tail = body[: link.start()], body[link.start() :]
            # Require a single lexical operand; an earlier residual might precede this connective.
            if len(list(current_vocabulary().cut(new))) != 1:
                return None
        else:
            tokens = list(current_vocabulary().cut(body))
            if not tokens:
                return None
            new, tail = tokens[0], "".join(tokens[1:])
            # Without a linker, split only if the new lexical unit aligns with its source.
            # Multiword new names remain undecided unless quotes or another signal bound them.
            if tail and old and not (len(new) == len(old) and any(a == b for a, b in zip(old, new))):
                return None
            if tail and re.match(r"有限公司|集团|公司|中心|店|馆|路|街|站|学校|先生|女士|老师", tail):
                return None
    if not old:
        old = implicit_anchor(text, new)
    if not old or not new:
        return None
    op = scope_change(text, old, new, scope)
    if not op:
        return None
    return op, u[: len(u) - len(tail)] if tail else u, tail, False


def plan(inp):
    match = parse(inp)
    if match is None:
        return {"mode": "whole", "method": "完整口述交给模型"}
    op, head, tail, alias = match
    marked, slots = tasks.mark_candidates(inp["editable_text"], op, alias)
    return {
        "mode": "staged",
        "method": "按字位先替换",
        "head": head,
        "tail": tail,
        "old": op.old,
        "new": op.new,
        "spans": op.spans,
        "draft": op.apply(inp["editable_text"]),
        "marked": marked,
        "markers": slots,
    }
