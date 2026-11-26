"""Route a spoken edit, ground any proposed changes and deliver one final body."""

import json
import re
from . import spelling, tasks, word_edits
from .prompts import prompt
from .review import aligned, ask, line_missing, repair_tail, review_change
from .vocabulary import current_vocabulary

SPOKEN_PREFIX = re.compile(
    r"^(?:嗯|好|那|就|请|帮我|麻烦|直接|还是|把它|把这个|把那个|这(?:个|里)|那个|给我|先)*"
    + r"(?:"
    + spelling.VERB
    + r"|替换为|换为)"
)
SAFE_OTHER = {
    "从末尾删除一句",
    "清空当前输入",
    "紧邻原文锚词插入",
    "句子范围",
    "语气词删除",
    "并列删除",
    "字符计数",
    "末尾指定字",
    "指定次序的标点",
}
CONTROL = re.compile(
    r"如果|假如|除非|算了|撤回|先别|只(?:动|在|限)|原样保留|引号里|引号内|不包括|除了"
)
CONFIRM = {
    "type": "json_schema",
    "json_schema": {
        "name": "confirm_edit",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "decision": {"type": "string", "enum": ["accept", "revise"]},
                "modified_text": {"type": "string"},
            },
            "required": ["decision", "modified_text"],
            "additionalProperties": False,
        },
    },
}

RESOLVE_SCHEMA = {
    "type": "json_schema",
    "json_schema": {
        "name": "resolve_target",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "decision": {"type": "string", "enum": ["replace", "keep", "whole"]},
                "old": {"type": "string"},
                "new": {"type": "string"},
                "occurrence": {"type": "integer"},
                "tail": {"type": "string"},
            },
            "required": ["decision", "old", "new", "occurrence", "tail"],
            "additionalProperties": False,
        },
    },
}
ROLE = re.compile(
    r"那(?:位|个|个人|一处|边)|这(?:位|个人|一处)|的(?:动作|人名|称呼|地方|名字|那个|那位)|负责.{1,12}(?:的|那位)"
)
FORMAT = re.compile(
    r"(?:英语|英文|韩语|韩文|中文|法语|日语|德语|日文|腔|风格|语气|邮件|故事|文章|新闻|格式|正式|口语|语调|报告|短文|表格|书面|过去式)"
)
CHOICE = {
    "type": "json_schema",
    "json_schema": {
        "name": "select_location",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {"index": {"type": "integer"}},
            "required": ["index"],
            "additionalProperties": False,
        },
    },
}
QUOTED = re.compile(r'“[^”]*”|「[^」]*」|『[^』]*』|‘[^’]*’|"[^"]*"')
CANCEL = re.compile(r"(?:算了|还是)(?:先)?(?:别改了|不改了|不要改了|保持原样)$")
ORDINAL = re.compile(
    r"^(?:把|将)?(?:第[一二两三四五六七八九十0-9]+|上[一个]?|下[一个]?|最后一|最前一)(?:趟|批|班|组|条|行|段|项|场|家|处|笔|轮|份|位)(?:的)?(?:"
    + spelling.VERB
    + r")"
)
SPELL = re.compile(
    r"^(?:请)?(?:把)?("
    + spelling.HAN
    + r"{2,10}?)的("
    + spelling.HAN
    + r")(?:是|就是)(.+?的"
    + spelling.HAN
    + r")$"
)
LAST = re.compile(r"^(?:请|帮我)?(?:把)?(?:最后|末尾)(?:的)?一(?:个)?句(?:话|子)?(?:删掉|删除|去掉)$")


def confirmed(inp, p):
    head = p["head"]
    if SPOKEN_PREFIX.match(head):
        return False
    m = spelling.EXPLICIT.fullmatch(head)
    if m and m["old"] == p["old"]:
        return True
    text = inp["editable_text"]
    if "的" not in head:
        return False
    if text.count(p["old"]) > 1:
        # Repeated scopes can be selected only by an overt ordinal, an exact
        # named source before the spelling clue, or an explicit universal.
        if re.search(spelling.SCOPE, head):
            return True
        return re.match(r"^(?:把)?" + re.escape(p["old"]) + r"(?:是|就是|改成|换成)", head) is not None
    # If the old form was named, its literal occurrence is evidence.
    lead = re.split(r"是|视|试|改成|换成", head, maxsplit=1)[0]
    if p["old"] in lead:
        return True
    # Otherwise require a unique sound-matching span or an explicit ordinal.
    if re.search(spelling.SCOPE, head):
        return True
    width = len(p["old"])
    hits = [
        i
        for i in range(len(text) - width + 1)
        if re.fullmatch(spelling.HAN + "+", text[i : i + width])
        and word_edits.sound(text[i : i + width]) == word_edits.sound(p["old"])
    ]
    return width == len(p["new"]) and len(hits) == 1


def has_anchor(text, old, word):
    if not old or not word or old not in text:
        return False
    if old in word:
        return True
    width = len(old)
    return (
        sum(
            1
            for i in range(len(text) - width + 1)
            if re.fullmatch(spelling.HAN + "+", text[i : i + width])
            and word_edits.sound(old) == word_edits.sound(text[i : i + width])
        )
        == 1
    )


def short_target(text):
    link = spelling.CONNECT.search(text)
    if link:
        text = text[: link.start()]
    if any(quote in text for quote in spelling.QUOTES):
        return ""
    tok = next(current_vocabulary().cut(text), "")
    return tok if re.fullmatch(spelling.HAN + "{1,8}", tok) else ""


def semantic(inp, complete, reason):
    response = ask(complete, prompt("location").strip(), inp, operation="location")
    return dict(mode="semantic", method=reason), response["modified_text"], "按完整口述交付"


def reviewed(inp, plan, complete):
    payload = {
        "原文": inp["editable_text"],
        "完整口述": inp["utterance"],
        "候选初稿": plan["draft"],
        "屏幕资料": inp["screen_context"],
        "保存资料": inp.get("saved_context", ""),
    }
    choice = ask(complete, prompt("confirm").strip(), payload, CONFIRM, operation="confirm")
    return (
        plan,
        plan["draft"] if choice["decision"] == "accept" else choice["modified_text"],
        "确认回填",
    )


def explicit_or_task(inp, complete):
    source = inp["editable_text"]
    u = spelling.spoken(inp["utterance"])
    if CONTROL.search(u) and re.search(spelling.VERB + "|的" + spelling.HAN, u):
        return semantic(inp, complete, "带范围或撤回条件，交给模型")
    if SPOKEN_PREFIX.match(u):
        # Creation and transformations with an explicit output format use full original text.
        if re.search("故事|情节|邮件|文章|短文", u):
            task = tasks.plan(inp)
            if task["mode"] in ("compose", "style", "write"):
                return tasks.run_task(inp, task, complete)
        return semantic(inp, complete, "省略原词，交给模型 从整句定位")
    p = spelling.plan(inp)
    old = tasks.plan(inp)
    mode, method = old["mode"], old["method"]
    m = spelling.EXPLICIT.fullmatch(u)
    explicit = m["old"] if m else ""
    conflict = p["mode"] == "staged" and explicit in source and explicit and p["old"] != explicit
    if m and not explicit:
        return semantic(inp, complete, "原词未给出")
    # Exact self-corrections and exact named pieces are safe proposals. A piece of a
    # spelling clue must never displace the old word the speaker actually named.
    if mode == "local" and method in ("正反纠正", "改口与量词", "最终改口", "逐字线索", "同音字位"):
        if method == "正反纠正":
            mneg = re.fullmatch("(" + spelling.HAN + "{1,10})不是(" + spelling.HAN + "{1,10})", u)
            certain = bool(
                mneg and mneg[2] in source and source.replace(mneg[2], mneg[1]) == old["draft"]
            )
        elif method == "同音字位":
            certain = p["mode"] == "staged" and confirmed(inp, p) and p["draft"] == old["draft"]
        else:
            certain = bool(explicit and explicit in source and explicit not in old["draft"])
        if certain:
            return reviewed(inp, dict(mode="confirmed", method=method, draft=old["draft"]), complete)
    if conflict:
        return semantic(inp, complete, "释字匹配与点名旧词冲突，整句判断")
    if p["mode"] == "staged" and confirmed(inp, p):
        output, reason = review_change(inp, p, complete, staged=True)
        return p, output, reason
    if mode == "term" and old.get("patch"):
        anchor = old["patch"][0]
        if source.count(anchor) == 1 and has_anchor(source, anchor, u):
            # The model chooses the spelling; the original term branch only writes its span.
            return tasks.run_task(inp, old, complete)
    if mode == "local":
        if method in SAFE_OTHER or method == "字词或符号定位" and not re.search(spelling.VERB, u):
            return tasks.run_task(inp, old, complete)
        return semantic(inp, complete, "未得到明确原词或可靠字位")
    if mode in ("polish", "translate", "term"):
        return semantic(inp, complete, "不能安全固定原词或字位")
    # For unanchored commands use the existing full-context task prompt. It contains
    # no preselected old word and emits the whole output. Keep semantic source selection.
    return tasks.run_task(inp, old, complete)


def resolve_or_edit(inp, complete):
    u = spelling.spoken(inp["utterance"])
    text = inp["editable_text"]
    missing = SPOKEN_PREFIX.match(u)
    role = ROLE.search(u)
    has_edit = re.search(spelling.VERB, u)
    target_body = u[has_edit.end() :] if has_edit else ""
    resolve = (missing and not FORMAT.search(target_body[:8])) or (
        role and (has_edit or re.search("是.{1,12}的" + spelling.HAN, u))
    )
    if not resolve or CONTROL.search(u) or not text.strip():
        plan, output, decision = explicit_or_task(inp, complete)
        if plan["mode"] == "staged" and line_missing(plan["tail"], output):
            output = repair_tail(inp, plan, output, complete)
        return plan, output, decision
    new_hint = short_target(target_body)
    hints = []
    if len(new_hint) >= 2:
        for pos in range(len(text) - len(new_hint) + 1):
            word = text[pos : pos + len(new_hint)]
            if (
                re.fullmatch(spelling.HAN + "+", word)
                and sum(a != b for a, b in zip(word, new_hint)) == 1
                and word not in hints
            ):
                hints.append(word)
    data = {**inp, "可能的原文片段（可全部舍弃）": hints}
    response = ask(complete, prompt("resolve").strip(), data, RESOLVE_SCHEMA, operation="resolve")
    old, new, which, tail = (response[n] for n in ["old", "new", "occurrence", "tail"])
    p = dict(mode="resolve", method="模型从整句提取确切原词", candidates=hints, selection=response)
    if response["decision"] == "whole":
        _, out, decision = semantic(inp, complete, "整体任务")
        return p, out, decision
    if response["decision"] == "keep":
        return p, text, "模型判断缺少可替换对象"
    # If the user explicitly names the absent old action/noun, a similar word in
    # unrelated material cannot take its place. Homophonic transcription is allowed.
    named = re.search(
        r"(?:把|将)(?:前面那个|后面那个|前面的|后面的|这个|那个)?("
        + spelling.HAN
        + r"{1,10}?)的(?:动作|人名|称呼|名字)(?:给)?(?:"
        + spelling.VERB
        + r")",
        u,
    )
    if named and old != named[1] and word_edits.sound(old) != word_edits.sound(named[1]):
        return p, text, "口述点名的对象与所选原词不符"
    if not old or not new or old == new or old not in text or which < 0:
        return p, text, "所选词缺乏可回填原位"
    op = tasks.change(text, old, new)
    if which:
        if which > len(op.spans):
            return p, text, "找不到所指次序"
        op = tasks.Change(old, new, (op.spans[which - 1],))
    marked, slots = tasks.mark_candidates(text, op, False)
    p.update(
        old=old, new=new, spans=op.spans, tail=tail, draft=op.apply(text), marked=marked, markers=slots
    )
    out, decision = review_change(inp, p, complete)
    return p, out, decision


def select_location(inp, complete):
    text = inp["editable_text"]
    u = spelling.spoken(inp["utterance"])
    prefix = SPOKEN_PREFIX.match(u)
    tail_body = u[prefix.end() :] if prefix else ""
    # A requested format, length, or topic is a whole-text transformation.
    if prefix and (
        FORMAT.search(tail_body[:8])
        or re.search(r"[0-9一二三四五六七八九十百]+个?字|相关的|为主题", tail_body)
    ):
        return explicit_or_task(inp, complete)
    if (
        not prefix
        and not re.search(spelling.VERB, u)
        and ROLE.search(u)
        and not re.search("那位|那个人|负责", u)
    ):
        return explicit_or_task(inp, complete)
    new = short_target(tail_body) if prefix else ""
    if len(new) < 2 or not text or CONTROL.search(u):
        return resolve_or_edit(inp, complete)
    tail = tail_body[len(new) :]
    if tail and not (
        spelling.CONNECT.match(tail) or re.search(r"一点|一些|.{0,6}些|分行|分开|语气|润色", tail)
    ):
        return resolve_or_edit(inp, complete)
    opts = []
    for i in range(len(text) - len(new) + 1):
        word = text[i : i + len(new)]
        if re.fullmatch(spelling.HAN + "+", word) and sum(a != b for a, b in zip(word, new)) == 1:
            if len(opts) == 12:
                return resolve_or_edit(inp, complete)
            opts.append((i, word, text[:i] + new + text[i + len(new) :]))
    if not opts:
        return resolve_or_edit(inp, complete)
    payload = {
        **inp,
        "用户给出的目标词": new,
        "备选": [
            {"编号": n, "原词": word, "仅替换后的全文": draft}
            for n, (i, word, draft) in enumerate(opts, 1)
        ],
    }
    selection = ask(complete, prompt("choose").strip(), payload, CHOICE, operation="choose")["index"]
    p = dict(
        mode="choose",
        method="由 模型比较逐处回填后的完整句子",
        choices=payload["备选"],
        index=selection,
        tail=tail,
    )
    if selection < 1 or selection > len(opts):
        return p, text, "没有足够证据选定位置"
    i, old, draft = opts[selection - 1]
    op = tasks.Change(old, new, ((i, i + len(old)),))
    marked, slots = tasks.mark_candidates(text, op, False)
    p.update(old=old, new=new, spans=op.spans, draft=draft, marked=marked, markers=slots)
    out, decision = review_change(inp, p, complete)
    return p, out, decision


def edit_scoped(inp, complete):
    u = spelling.spoken(inp["utterance"])
    visible = QUOTED.sub("□", u)
    stop = CANCEL.search(visible)
    if stop:
        head = visible[: stop.start()]
        verbs = list(re.finditer(spelling.VERB, head))
        if (
            len(verbs) == 1
            and head[verbs[0].end() :]
            and not re.search(r"然后|接着|另外|并且|同时|再|润色|分行|翻译", head)
        ):
            return (
                dict(mode="cancel", method="唯一替换请求在句尾被明确撤回"),
                inp["editable_text"],
                "保留撤回前原文",
            )
    if ORDINAL.match(visible):
        # A trip, paragraph or batch can designate a surrounding unit instead of
        # the substring to overwrite. The model first selects a value within it.
        data = {
            **inp,
            "可能的原文片段（可全部舍弃）": [],
            "定位说明": "序数及其单位表示用户所指的那一项。保留序号和其他项目，找出该项中与新词承担相同角色的原值。",
        }
        res = ask(complete, prompt("resolve").strip(), data, RESOLVE_SCHEMA, operation="ordinal")
        old, new, idx, tail = (res[n] for n in ("old", "new", "occurrence", "tail"))
        p = dict(mode="ordinal_scope", method="从所指项目里面选择原值", selection=res)
        if (
            res["decision"] != "replace"
            or not old
            or not new
            or old not in inp["editable_text"]
            or ORDINAL.match(old + "改成")
            or old == new
            or idx < 0
        ):
            return p, inp["editable_text"], "未确认所指项目中的原值"
        op = tasks.change(inp["editable_text"], old, new)
        if not op or idx > len(op.spans):
            return p, inp["editable_text"], "所指原值或次序无法核实"
        if idx:
            op = tasks.Change(old, new, (op.spans[idx - 1],))
        marked, slots = tasks.mark_candidates(inp["editable_text"], op, False)
        p.update(
            old=old,
            new=new,
            spans=op.spans,
            tail=tail,
            draft=op.apply(inp["editable_text"]),
            marked=marked,
            markers=slots,
        )
        out, decision = review_change(inp, p, complete)
        return p, out, decision
    return select_location(inp, complete)


def edit(inp, complete):
    return edit_with_steps(inp, lambda messages, schema, operation: complete(messages, schema))


def edit_with_steps(inp, complete):
    text = inp["editable_text"]
    u = spelling.spoken(inp["utterance"])
    if text.strip() and LAST.fullmatch(u):
        p, outcome = tasks.local_or_polish(dict(inp, utterance="删除最后一句"))[:2]
        return dict(mode="local", method=p, draft=outcome), outcome, "按完整句界删除最后一句"
    clue = SPELL.fullmatch(u)
    if clue:
        anchor, old, phrase = clue.groups()
        glyphs = word_edits.pairs(phrase)
        if (
            text.count(anchor) == 1
            and anchor.count(old) == 1
            and len(glyphs) == 1
            and len(glyphs[0]) == 1
            and old != glyphs[0]
        ):
            i = text.index(anchor) + anchor.index(old)
            op = tasks.Change(old, glyphs[0], ((i, i + 1),))
            marked, slots = tasks.mark_candidates(text, op, False)
            p = dict(
                mode="scoped_glyph",
                method="只回填点名词内部的字位",
                old=old,
                new=glyphs[0],
                tail="",
                spans=op.spans,
                draft=op.apply(text),
                marked=marked,
                markers=slots,
            )
            out, decision = review_change(inp, p, complete)
            return p, out, decision
    last_response = ""

    def record(messages, schema, operation):
        nonlocal last_response
        last_response = complete(messages, schema, operation)
        return last_response

    p, out, decision = edit_scoped(inp, record)
    if (
        not p.get("tail")
        or not p.get("draft")
        or out != p["draft"]
        or len(p.get("new", "")) != 2
        or len(p.get("markers", [])) != 1
    ):
        return p, out, decision
    # A separable verb may be written “A一下B” when asking politely. Restore the
    # exact requested term before applying the same span validation as before.
    last = json.loads(last_response).get("modified_text", "") if last_response else ""
    if "⟦" in last or not last:
        return p, out, decision
    word = p["new"]
    infix = re.compile(re.escape(word[0]) + r"(?:一下|一下子|一会儿)" + re.escape(word[1]))
    if len(infix.findall(last)) == 1:
        fixed = infix.sub(word, last)
        if aligned(p["draft"], fixed, p):
            return p, fixed, "复核请求语气并保留原定词形"
    return p, out, decision
