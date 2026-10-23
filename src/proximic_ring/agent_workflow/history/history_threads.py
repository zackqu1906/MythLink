"""Bounded, read-only Codex conversation metadata used only to resolve targets."""
import json

from ..recording.codex_link import codex_thread_url
from .history_source import MAX_TEXT, digest, redact, text

MAX_THREADS = 20
THREAD_TEXT = 6000


def select_threads(rows):
    result, seen, size = [], set(), 0
    for row in rows:
        if not isinstance(row, dict):
            continue
        url = codex_thread_url(row.get("id"))
        title = row.get("name")
        if (not url or row.get("ephemeral") is not False or row.get("archived")
                or not isinstance(title, str) or not title.strip() or len(title) > 300
                or redact(title) != title):
            continue
        tid = url.rsplit("/", 1)[-1]
        if tid in seen:
            continue
        candidate = dict(thread_id=tid, title=title, cwd=text(row.get("cwd"), 300),
                         preview=text(row.get("preview"), 200))
        cost = len(json.dumps(candidate, ensure_ascii=False))
        if size + cost > THREAD_TEXT:
            continue
        result.append(candidate)
        seen.add(tid)
        size += cost
        if len(result) == MAX_THREADS:
            break
    return result


def with_threads(evidence, threads):
    """Share the existing evidence budget; a catalog alone is not activity."""
    result = dict(evidence, codex_threads=threads,
                  summaries=list(evidence.get("summaries", [])), events=list(evidence["events"]))
    cost = lambda rows: sum(len(json.dumps(row, ensure_ascii=False)) for row in rows)
    remaining = MAX_TEXT - cost(threads)
    while result["events"] and cost(result["summaries"] + result["events"]) > remaining:
        result["events"].pop(0)  # keep the newest detail first
    while result["summaries"] and cost(result["summaries"]) > remaining:
        result["summaries"].pop()
    result["source_at"] = max((row["at"] for row in result["summaries"] + result["events"]), default=0)
    if threads:
        result["fingerprint"] = digest([evidence["fingerprint"], threads])
    return result
