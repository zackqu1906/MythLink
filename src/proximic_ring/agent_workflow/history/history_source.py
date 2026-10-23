"""Memory-first Computer History context with a bounded recent event supplement."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re

from ..recording.recording import redact

DETAIL_WINDOW_SECONDS = 20 * 60  # raw tail only; not the recommendation horizon
MAX_BYTES = 2 * 1024 * 1024
MAX_TEXT = 18000
SUMMARY_TEXT = 12000
HUD_TITLE = "Mythlink · 下一步"


class HistoryUnavailable(ValueError):
    pass


def timestamp(value):
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.timestamp() if parsed.tzinfo else 0
    except (ValueError, TypeError, OverflowError):
        return 0


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def text(value, limit):
    return redact(value[:limit]) if isinstance(value, str) else ""


def memory_resources():
    return Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex") / "memories/extensions/skysight/resources"


def read_summaries(root, now):
    """Select recent broad summaries plus detail summaries, never all memories.

    Filenames carry the activity timestamp. A copied file's mtime must not make
    an old memory appear new. Source timestamps stay attached to model evidence.
    """
    root = Path(root).resolve()
    if not root.is_dir():
        return []
    candidates = []
    for path in root.glob("*.md"):
        kind = "6h" if "-6h-" in path.name else "10min" if "-10min-" in path.name else None
        match = re.match(r"^(\d{4}-\d{2}-\d{2})T(\d{2})[-:](\d{2})[-:](\d{2})", path.name)
        if not kind or not match or not path.resolve().is_relative_to(root):
            continue
        date, hour, minute, second = match.groups()
        at = timestamp(f"{date}T{hour}:{minute}:{second}Z")
        if not 0 < at <= now + 30:
            continue
        candidates.append((at, path, kind))
    candidates.sort(key=lambda row: (row[0], row[1].name), reverse=True)
    chosen = ([row for row in candidates if row[2] == "6h"][:2]
              + [row for row in candidates if row[2] == "10min"][:6])
    result, seen, remaining = [], set(), SUMMARY_TEXT
    for at, path, kind in chosen:
        if remaining < 400:
            break
        try:
            with path.open("rb") as stream:
                raw = stream.read(32 * 1024)
            content = text(raw.decode("utf-8", errors="replace"), min(3200, remaining - 250))
        except OSError:
            continue  # memories can be deleted/replaced while we enumerate
        if not content.strip() or digest(content) in seen:
            continue
        seen.add(digest(content))
        row = dict(kind=kind, at=at, time=datetime.fromtimestamp(at, timezone.utc).isoformat(),
                   ref=digest([kind, at, content])[:16], text=content)
        cost = len(json.dumps(row, ensure_ascii=False))
        if cost > remaining:
            continue
        remaining -= cost
        result.append(row)
    return result


def _read_events(status, now, budget):
    root_value = status.get("eventStreamRootPath")
    if not isinstance(root_value, str) or not Path(root_value).is_absolute():
        raise HistoryUnavailable("Computer History 没有提供活动目录")
    root = Path(root_value).resolve()
    segments = root / "segments"
    if not segments.is_dir():
        raise HistoryUnavailable("Computer History 的活动目录暂不可用")
    # Segment names are UTC start times. Read at most the latest four recent
    # segments, each with a bounded tail; never scan the user's whole history.
    files = []
    for directory in sorted(segments.iterdir(), reverse=True):
        if len(files) == 4:
            break
        path = directory / "events.jsonl"
        if (path.is_file() and path.resolve().is_relative_to(segments.resolve())
                and now - path.stat().st_mtime <= DETAIL_WINDOW_SECONDS + 600):
            files.append(path)
    events = []
    for path in files:
        with path.open("rb") as stream:
            size = os.fstat(stream.fileno()).st_size
            stream.seek(max(0, size - MAX_BYTES))
            if size > MAX_BYTES:
                stream.readline()  # discard possibly partial leading record
            data = stream.read(MAX_BYTES)
        for line in data.splitlines(keepends=True):
            if not line.endswith(b"\n"):
                continue  # writer may still be appending this event
            try:
                event = json.loads(line)
                at = timestamp(event.get("timestamp"))
                if not now - DETAIL_WINDOW_SECONDS <= at <= now + 30:
                    continue
                app, window = event.get("app") or {}, event.get("window") or {}
                if app.get("processIdentifier") == os.getpid() or window.get("title") == HUD_TITLE:
                    continue
                ax = event.get("ax") or {}
                target = (event.get("selection") or event.get("mouse") or {}).get("target") or {}
                if "secure" in str(target.get("role", "")).lower():
                    continue
                body = {
                    "app": text(app.get("name"), 80),
                    "window": text(window.get("title"), 200),
                    "url": text(window.get("url"), 400),
                    "kind": text(event.get("kind"), 60),
                    "target": text(target.get("title") or target.get("description"), 240),
                    "ax_mode": text(ax.get("mode"), 40),
                    "ax_excerpt": text(ax.get("text"), 1600),
                }
                if not any(body[key] for key in ("window", "target", "ax_excerpt")):
                    continue
                events.append((at, body))
            except (ValueError, TypeError, AttributeError):
                continue
    # Input order is not guaranteed by the history writer. Deduplicate repeated
    # UI snapshots before hashing, so idle mouse clicks don't trigger inference.
    events.sort(key=lambda entry: entry[0], reverse=True)
    selected, seen, size = [], set(), 0
    for at, body in events:
        key = digest(body)
        if key in seen:
            continue
        seen.add(key)
        row = dict(body, at=at, ref=digest([at, body])[:16])
        cost = len(json.dumps(row, ensure_ascii=False))
        if size + cost > budget:
            continue
        selected.append(row)
        size += cost
        if len(selected) == 40:
            break
    selected.reverse()
    return selected


def read_history(status, now, *, summaries_root=None):
    state = status.get("state")
    if state not in ("running", "paused", "stopped"):
        raise HistoryUnavailable("Computer History 状态暂不可用")
    summaries = read_summaries(memory_resources() if summaries_root is None else summaries_root, now)
    summary_size = sum(len(json.dumps(row, ensure_ascii=False)) for row in summaries)
    events = []
    if state == "running":
        try:
            events = _read_events(status, now, MAX_TEXT - summary_size)
        except (OSError, HistoryUnavailable):
            if not summaries:
                raise
    # Pausing collection does not delete existing memories. Recommendations
    # can use summaries without resuming capture or reading raw events.
    sources = summaries + events
    return {
        "summaries": summaries,
        "events": events,
        "collection_state": state,
        "fingerprint": digest([state, [row["ref"] for row in summaries],
                              sorted(digest({k: v for k, v in row.items() if k not in ("at", "ref")})
                                     for row in events)]) if sources else "",
        "source_at": max((row["at"] for row in sources), default=0),
        "captured_at": now,
        "as_of": datetime.fromtimestamp(now, timezone.utc).isoformat(),
    }
