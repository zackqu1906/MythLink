"""Local evidence recorder. Never executes tools or asks a model to reconstruct facts.

The append-only journal is the evidence source; SQLite is a rebuildable read index.
Disk work and attachment decoding run on one worker, outside the RPC/control loop.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import platform
import queue
import re
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from proximic_ring.runtime_paths import app_data_root

SCHEMA_VERSION = 1
MAX_EVENT_BYTES = 48 * 1024 * 1024
MAX_QUEUE_BYTES = 96 * 1024 * 1024
IMAGE_TYPES = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp", "image/gif": ".gif"}
SECRET_KEYS = {"token", "accesstoken", "refreshtoken", "idtoken", "apikey", "password",
               "passwd", "secret", "clientsecret", "authorization", "cookie", "setcookie",
               "encryptedcontent", "credential", "credentials"}
SECRET_TEXT = re.compile(
    r"(?i)(\b(?:Bearer\s+)[\w.\-]+|\bsk-[A-Za-z0-9_-]{12,}|"
    r"\b(?:access_token|refresh_token|api_key|password|client_secret)=([^\s&\"']+))")


def now():
    return datetime.now(timezone.utc).isoformat()


def records_root():
    return app_data_root() / "agent-experience"


def redact(value):
    """Structural credentials and recognizable credential literals, not a DLP claim."""
    if isinstance(value, dict):
        return {k: ("[REDACTED]" if re.sub(r"[^a-z]", "", k.lower()) in SECRET_KEYS
                    else redact(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    if isinstance(value, str):
        return SECRET_TEXT.sub("[REDACTED]", value)
    return value


class ExperienceRecorder:
    def __init__(self, root=None, *, metadata=None, save_images=True,
                 max_queue_bytes=MAX_QUEUE_BYTES):
        self.root = Path(root) if root is not None else records_root()
        self.session_id = uuid.uuid4().hex
        self.directory = self.root / "sessions" / self.session_id
        self.save_images = save_images
        self.max_queue_bytes = max_queue_bytes
        self._queue = queue.Queue()
        self._lock = threading.Lock()
        self._bytes = self._seq = self._dropped = self._reported_drops = 0
        self._closed = False
        self._error = ""
        self._ready = threading.Event()
        self._metadata = dict(schema_version=SCHEMA_VERSION, session_id=self.session_id,
            created_at=now(), status="recording", pid=os.getpid(), platform=platform.platform(),
            timezone=str(datetime.now().astimezone().tzinfo), save_images=save_images,
            summary_generation="disabled", **(metadata or {}))
        self._worker = threading.Thread(target=self._run, name="AgentExperienceRecorder", daemon=True)
        self._worker.start()

    @property
    def health(self):
        with self._lock:
            return {"recordingState": "error" if self._error else "degraded" if self._dropped else "recording",
                    "recordingError": self._error, "recordingDroppedEvents": self._dropped,
                    "recordingDirectory": str(self.directory), "recordsRoot": str(self.root)}

    def _submit(self, direction, method, payload, episode_id=None, thread_id=None):
        try:
            # Freeze buffers before the caller mutates state. No disk I/O here.
            encoded = json.dumps(payload, ensure_ascii=False)
            size = len(encoded.encode("utf-8"))
        except (TypeError, ValueError, RecursionError):
            with self._lock:
                self._dropped += 1
            return
        with self._lock:
            if self._closed:
                return
            if self._ready.is_set() and not self._worker.is_alive():
                self._dropped += 1
                return
            self._seq += 1
            if size > MAX_EVENT_BYTES or self._bytes + size > self.max_queue_bytes:
                self._dropped += 1
                return
            self._bytes += size
            self._queue.put((dict(schema_version=SCHEMA_VERSION, session_id=self.session_id,
                event_id=uuid.uuid4().hex, seq=self._seq, at=now(), monotonic_ns=time.monotonic_ns(),
                direction=direction, method=method, episode_id=episode_id,
                thread_id=thread_id, association="client_context" if episode_id else "session"), encoded, size))

    def rpc(self, direction, message, *, episode_id=None, thread_id=None):
        self._submit(direction, message.get("method", "rpc/response"), message, episode_id, thread_id)

    def local(self, kind, payload=None, *, episode_id=None, thread_id=None):
        self._submit("local", kind, payload or {}, episode_id, thread_id)

    def new_episode(self, goal, source, context=None):
        from proximic_ring.agent_workflow.recommendation.recommendation_context import clock_context
        context = dict(context or {"availability": "not_captured"})
        context.setdefault("time_context", clock_context())
        episode = uuid.uuid4().hex
        self.local("episode/created", {"goal": goal, "source": source,
                   "context": context or {"availability": "not_captured"}}, episode_id=episode)
        return episode

    def flush(self, timeout=5):
        if not self._worker.is_alive():
            return False
        barrier = threading.Event()
        self._queue.put(barrier)
        return barrier.wait(timeout)

    def close(self, timeout=5):
        with self._lock:
            if not self._closed:
                self._closed = True
                self._queue.put(None)
        self._worker.join(timeout)
        if self._worker.is_alive():
            with self._lock:
                self._error = "记录写入尚未完成，关闭时可能缺失尾部事件"

    def _blob(self, data, mime, quality):
        try:
            raw = base64.b64decode(data, validate=True)
        except (ValueError, TypeError):
            quality.append("invalid_image_data")
            return {"attachment": None, "mime_type": mime, "availability": "invalid_base64"}
        digest = hashlib.sha256(raw).hexdigest()
        result = {"sha256": digest, "mime_type": mime, "size_bytes": len(raw)}
        if not self.save_images or mime not in IMAGE_TYPES:
            quality.append("image_not_saved")
            return {**result, "availability": "not_saved"}
        relative = "attachments/" + digest + IMAGE_TYPES[mime]
        path = self.directory / relative
        if not path.exists():
            path.write_bytes(raw)
        return {**result, "attachment": relative, "availability": "saved"}

    def _sanitize(self, value, quality, depth=0):
        if depth > 80:
            quality.append("max_depth_exceeded")
            return {"omitted": "max_depth_exceeded"}
        if isinstance(value, dict):
            if value.get("type") == "image" and isinstance(value.get("data"), str):
                mime = value.get("mimeType", "application/octet-stream")
                metadata = {k:self._sanitize(v,quality,depth+1) for k,v in value.items() if k!='data'}
                return {**metadata, **self._blob(value["data"], mime, quality)}
            # Store only public summaries. Never turn hidden reasoning into evidence.
            value = dict(value)
            if value.get("type") == "reasoning":
                for key in ("content", "encrypted_content"):
                    if value.get(key):
                        quality.append("non_summary_reasoning_omitted")
                    value.pop(key, None)
            out = {}
            for key, child in value.items():
                if re.sub(r"[^a-z]", "", key.lower()) in SECRET_KEYS:
                    out[key] = "[REDACTED]"
                    quality.append("credential_redacted")
                else:
                    out[key] = self._sanitize(child, quality, depth + 1)
            return out
        if isinstance(value, list):
            return [self._sanitize(v, quality, depth + 1) for v in value]
        if isinstance(value, str):
            if value.startswith("data:image/") and ";base64," in value:
                header, data = value.split(";base64,", 1)
                return self._blob(data, header[5:], quality)
            clean = redact(value)
            if clean != value:
                quality.append("credential_redacted")
            return clean
        return value

    def _run(self):
        db = journal = None
        try:
            self.root.mkdir(parents=True, mode=0o700, exist_ok=True)
            (self.root / 'sessions').mkdir(mode=0o700, exist_ok=True)
            self.directory.mkdir(parents=True, mode=0o700)
            (self.directory / "attachments").mkdir(mode=0o700)
            self._save_metadata()
            journal = (self.directory / "events.jsonl").open("a", encoding="utf-8")
            db = sqlite3.connect(self.directory / "index.sqlite3")
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE events (seq INTEGER PRIMARY KEY, event_id TEXT, at TEXT,
                    direction TEXT, method TEXT, episode_id TEXT, thread_id TEXT, turn_id TEXT,
                    item_id TEXT, request_id TEXT, association TEXT, payload TEXT, quality TEXT);
                CREATE INDEX events_episode ON events(episode_id, seq);
                CREATE TABLE episodes (id TEXT PRIMARY KEY, created_at TEXT, updated_at TEXT,
                    goal TEXT, source TEXT, status TEXT, goal_status TEXT DEFAULT 'unknown', context TEXT);
                CREATE TABLE items (key TEXT PRIMARY KEY, episode_id TEXT, thread_id TEXT,
                    turn_id TEXT, item_id TEXT, type TEXT, status TEXT, first_seq INTEGER,
                    last_seq INTEGER, payload TEXT);
                CREATE TABLE observations (id TEXT PRIMARY KEY, episode_id TEXT, item_key TEXT,
                    event_seq INTEGER, kind TEXT, payload TEXT);
            """)
            requests, server_requests, turns, item_links = {}, {}, {}, {}
            self._ready.set()
            while True:
                entry = self._queue.get()
                if entry is None:
                    break
                if isinstance(entry, threading.Event):
                    journal.flush(); db.commit(); self._save_metadata(); entry.set()
                    continue
                row, encoded, size = entry
                try:
                    payload = json.loads(encoded)
                    self._associate(row, payload, requests, server_requests, turns, item_links)
                    quality = []
                    if row.pop("omit_sensitive", False):
                        payload = {"omitted": "authentication_or_secret_response", "id": payload.get("id")}
                        quality.append("sensitive_payload_omitted")
                    if row["method"] == "item/reasoning/textDelta":
                        payload = {"omitted": "non_summary_reasoning"}
                        quality.append("non_summary_reasoning_omitted")
                    row["payload"] = self._sanitize(payload, quality)
                    row["quality"] = sorted(set(quality))
                    if row.get('response_to') in ('initialize','thread/start'):
                        result = row['payload'].get('result')
                        if isinstance(result, dict):
                            fields = ('userAgent','platformFamily','platformOs') if row['response_to']=='initialize' else (
                                'model','modelProvider','reasoningEffort','serviceTier','approvalPolicy','sandbox')
                            self._metadata.setdefault('agent_configuration',{}).update(
                                {k:result[k] for k in fields if k in result})
                    with self._lock:
                        drops = self._dropped
                    if drops != self._reported_drops:
                        row["recording_gap"] = {"dropped_events_total": drops}
                        self._reported_drops = drops
                    journal.write(json.dumps(row, ensure_ascii=False) + "\n")
                    journal.flush()
                    self._index(db, row)
                    db.commit()
                except Exception as exc:
                    with self._lock:
                        self._error = "记录写入异常：" + type(exc).__name__
                    if db:
                        db.rollback()
                finally:
                    with self._lock:
                        self._bytes -= size
            db.execute("UPDATE episodes SET status='unfinished' WHERE status IN ('queued','starting','running')")
            db.commit()
            self._metadata["status"] = "closed"
            self._metadata["closed_at"] = now()
            self._save_metadata()
        except Exception as exc:
            with self._lock:
                self._error = "无法创建工作记录：" + type(exc).__name__
        finally:
            self._ready.set()
            if journal:
                journal.close()
            if db:
                db.close()
            # Release callers even when initialization or a final flush failed.
            while not self._queue.empty():
                pending = self._queue.get()
                if isinstance(pending, threading.Event):
                    pending.set()

    def _save_metadata(self):
        meta = {**self._metadata, **self.health}
        target = self.directory / "session.json"
        temp = target.with_suffix(".tmp")
        temp.write_text(json.dumps(redact(meta), ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(target)

    def _associate(self, row, msg, requests, server_requests, turns, item_links):
        if row["direction"] == "local":
            row["turn_id"] = msg.get("turnId")
            row["request_id"] = msg.get("requestId")
            return
        p = msg.get("params") if isinstance(msg.get("params"), dict) else {}
        method = msg.get("method", "")
        outgoing = row["direction"] == "outgoing"
        if method.startswith(("account/", "auth/")):
            row["omit_sensitive"] = True
        request_id = msg.get("id")
        row["request_id"] = request_id
        row["thread_id"] = p.get("threadId") or row.get("thread_id")
        row["turn_id"] = p.get("turnId") or p.get("expectedTurnId") or (p.get("turn") or {}).get("id")
        row["item_id"] = p.get("itemId") or (p.get("item") or {}).get("id")
        if not method and request_id is not None:
            origin = (server_requests if outgoing else requests).get(str(request_id))
            if origin:
                origin['resolved'] = True
                for k in ("episode_id", "thread_id", "turn_id", "item_id"):
                    row[k] = origin.get(k)
                row["association"] = "request_id"
                row["response_to"] = origin["method"]
                row["omit_sensitive"] = origin.get("sensitive", False)
                result = msg.get("result")
                turn = (result.get("turn") or {}) if isinstance(result, dict) else {}
                if origin["method"] == "turn/start" and turn.get("id"):
                    row["turn_id"] = turn["id"]
                    turns[turn["id"]] = row.get("episode_id")
        turn_id = row.get("turn_id")
        if turn_id:
            if turn_id in turns:
                row["episode_id"] = turns[turn_id]
                row["association"] = "turn_id"
            elif method == "turn/started":
                pending = [r for r in requests.values() if r["method"] == "turn/start"
                           and not r.get('resolved') and r.get("episode_id") and r.get("episode_id") not in turns.values()]
                if len(pending) == 1:
                    row["episode_id"] = pending[0]["episode_id"]
                    row["association"] = "single_pending_start"
                    turns[turn_id] = row["episode_id"]
                else:
                    row["episode_id"] = None; row["association"] = "unresolved"
            else:
                row["episode_id"] = None; row["association"] = "unresolved"
        if row.get("item_id"):
            key = (row.get("thread_id"), row["item_id"])
            if row.get("episode_id"):
                item_links[key] = (row["episode_id"], row.get("turn_id"))
            elif key in item_links:
                row["episode_id"], row["turn_id"] = item_links[key]
                row["association"] = "item_id"
        if method and request_id is not None:
            sensitive = method.startswith(("account/", "auth/")) or any(
                q.get("isSecret") for q in p.get("questions", []) if isinstance(q, dict))
            origin = {k: row.get(k) for k in ("episode_id", "thread_id", "turn_id", "item_id")}
            origin.update(method=method, sensitive=sensitive)
            (requests if outgoing else server_requests)[str(request_id)] = origin
            if method.startswith(("account/", "auth/")):
                row["omit_sensitive"] = True

    def _index(self, db, row):
        payload = row["payload"]
        dump = lambda v: json.dumps(v, ensure_ascii=False)
        db.execute("INSERT INTO events VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", (
            row["seq"], row["event_id"], row["at"], row["direction"], row["method"],
            row.get("episode_id"), row.get("thread_id"), row.get("turn_id"), row.get("item_id"),
            str(row["request_id"]) if row.get("request_id") is not None else None,
            row["association"], dump(payload), dump(row["quality"])))
        episode, method = row.get("episode_id"), row["method"]
        if method == "episode/created":
            db.execute("INSERT INTO episodes(id,created_at,updated_at,goal,source,status,context) VALUES(?,?,?,?,?,?,?)",
                (episode, row["at"], row["at"], payload["goal"], payload["source"], "queued", dump(payload["context"])))
        if episode:
            status = None
            if method == "episode/starting": status = "starting"
            elif method == "turn/started": status = "running"
            elif method == "turn/completed": status = (payload.get("params", {}).get("turn") or {}).get("status")
            elif method == "episode/cancelled": status = "cancelled_before_start"
            elif row.get("response_to") == "turn/start" and payload.get("error"): status = "start_failed"
            db.execute("UPDATE episodes SET updated_at=? WHERE id=?", (row["at"], episode))
            if status:
                db.execute("UPDATE episodes SET status=? WHERE id=?", (status, episode))
        if row["direction"] != "incoming":
            return
        p = payload.get("params") if isinstance(payload.get("params"), dict) else {}
        item = p.get("item")
        item_id = row.get("item_id")
        if not item_id:
            return
        key = ":".join(str(row.get(k) or "") for k in ("thread_id", "turn_id", "item_id"))
        previous = db.execute("SELECT payload,first_seq,status FROM items WHERE key=?", (key,)).fetchone()
        current = json.loads(previous[0]) if previous else {"id": item_id}
        # Deltas remain in the journal; a completed item is the authoritative view.
        if isinstance(item, dict):
            current.update(item)
            if method == "item/completed":
                current["lifecycle"] = "completed"
        elif current.get("lifecycle") != "completed":
            if method == "item/agentMessage/delta":
                current.update(type="agentMessage", text=current.get("text", "") + p.get("delta", ""))
            elif method in ("item/reasoning/summaryTextDelta", "item/reasoning/summaryPartAdded"):
                current["type"] = "reasoning"
                summary = current.setdefault("summary", [])
                index = p.get("summaryIndex", 0)
                if isinstance(index, int) and 0 <= index < 10000:
                    while len(summary) <= index: summary.append("")
                    summary[index] += p.get("delta", "")
            elif method == "item/plan/delta":
                current.update(type="plan", text=current.get("text", "") + p.get("delta", ""))
            else:
                return
        else:
            return
        for field in ("startedAtMs", "completedAtMs"):
            if field in p: current[field] = p[field]
        db.execute("INSERT OR REPLACE INTO items VALUES (?,?,?,?,?,?,?,?,?,?)", (
            key, episode, row.get("thread_id"), row.get("turn_id"), item_id,
            current.get("type", "unknown"), current.get("status", current.get("lifecycle", "inProgress")),
            previous[1] if previous else row["seq"], row["seq"], dump(current)))
        if method == "item/completed" and current.get("type") in ("mcpToolCall", "dynamicToolCall"):
            result = current.get("result", current.get("contentItems"))
            if result is not None:
                db.execute("INSERT OR REPLACE INTO observations VALUES (?,?,?,?,?,?)", (
                    key + ":result", episode, key, row["seq"], "tool_result", dump(result)))
