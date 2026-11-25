"""Verify numbered replacements before accepting a model revision."""

from difflib import SequenceMatcher
import json
import re
from . import spelling, tasks
from .prompts import prompt

MARK = re.compile(r"⟦T(\d+)⟧([^⟦⟧]*)⟦/T\1⟧")


def normalize_marks(marked, slots):
    found = list(MARK.finditer(marked))
    if len(found) != len(slots):
        return None
    for mark, slot in zip(found, slots):
        if int(mark[1]) != slot["id"]:
            return None
        fragment = mark[2]
        if fragment in slot["allowed"]:
            continue
        # Widened delimiters are safe only for one unique literal slot. Repeated
        # or homophonic alternatives increase the risk of accepting a moved term.
        if len(slots) != 1 or len(slot["allowed"]) != 1 or fragment.count(slot["value"]) != 1:
            return None
        if slot["original"] != slot["value"] and slot["original"] in fragment:
            return None
    clean = MARK.sub(lambda m: m[2], marked)
    if "⟦" in clean or "⟧" in clean:
        return None
    if len(slots) == 1 and found[0][2] not in slots[0]["allowed"]:
        if clean.count(slots[0]["value"]) != 1 or slots[0]["original"] in clean:
            return None
    return clean


def aligned(draft, final, plan):
    if "⟦" in final or "⟧" in final:
        return False
    # Require every marked source position to survive within the text alignment.
    blocks = SequenceMatcher(None, draft, final, autojunk=False).get_matching_blocks()
    offset = 0
    last = 0
    for m, slot in zip(MARK.finditer(plan["marked"]), plan["markers"]):
        offset += m.start() - last
        value = slot["value"]
        if not any(a <= offset and offset + len(value) <= a + n for a, b, n in blocks):
            return False
        if draft.count(value) != final.count(value):
            return False
        offset += len(value)
        last = m.end()
    return True


STYLE = "在完成后续要求时，保留原文的时效、必要条件、数量和肯否立场。"
LINE = re.compile(r"分(?:开写|.{0,4}行)|各(?:占|写).{0,4}行|一.{1,3}一行|换行|另起")


def line_missing(tail, output):
    return bool(LINE.search(tail)) and "\n" not in output.strip()


def ask(complete, system, data, schema=None):
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps(data, ensure_ascii=False)},
    ]
    return json.loads(complete(messages, schema))


def repair_tail(inp, plan, output, complete, *, check_lines=True):
    tail = plan["tail"]
    data = {
        "完整口述": inp["utterance"],
        "已改好的正文": plan["draft"],
        "剩余口述": tail,
        "原文": inp["editable_text"],
        "屏幕资料": inp["screen_context"],
        "保存资料": inp.get("saved_context", ""),
    }
    system = prompt("repair").strip() + "\n" + STYLE
    if check_lines and line_missing(tail, output):
        system += "\n逐项确认排版，要求分行时用实际换行符隔开对应事项。"
    revised = ask(complete, system, data)["modified_text"]
    return revised if aligned(plan["draft"], revised, plan) else output


def review_change(inp, plan, complete, *, staged=False):
    data = {"完整口述": inp["utterance"], "原文": inp["editable_text"]}
    if staged:
        data["已处理口述"] = plan["head"]
    else:
        data.update(已处理的原词=plan["old"], 替换为=plan["new"])
    data.update(
        剩余口述=plan["tail"],
        初稿=plan["draft"],
        带编号的初稿=plan["marked"],
        各位置允许的写法=[s["allowed"] for s in plan["markers"]],
        屏幕资料=inp["screen_context"],
        保存资料=inp.get("saved_context", ""),
    )
    response = ask(complete, prompt("review").strip() + "\n" + STYLE, data, spelling.SCHEMA)
    body = response["modified_text"]
    if (
        response["decision"] == "translate"
        and (staged or plan["tail"])
        and not re.search(r"[⟦⟧]", body)
    ):
        if not staged:
            return body, "已完成后续翻译"
        output = body
    else:
        output = plan["draft"]
        if response["decision"] == "edit":
            if staged:
                clean = tasks.finish({"mode": "polish", "patch": plan}, inp, body)
                if clean is None and plan["tail"]:
                    clean = normalize_marks(body, plan["markers"])
            else:
                clean = normalize_marks(body, plan["markers"]) if plan["tail"] else None
            if clean is not None:
                output = clean
    if plan["tail"]:
        if staged and output == plan["draft"]:
            output = repair_tail(inp, plan, output, complete, check_lines=False)
        if not staged and (output == plan["draft"] or line_missing(plan["tail"], output)):
            output = repair_tail(inp, plan, output, complete)
    return output, "定位后复核并交付"
