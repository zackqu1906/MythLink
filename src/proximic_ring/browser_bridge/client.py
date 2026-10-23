"""Bounded native requests; no browser snapshots are cached in the AX worker."""
import json
import urllib.request
from .protocol import MAX_MESSAGE, PORT, VERSION


def post(path, data, token="", *, port=PORT, timeout=1.8):
    request = urllib.request.Request(f"http://127.0.0.1:{port}{path}",
        json.dumps({**data, "version": VERSION}).encode(),
        {"Content-Type": "application/json", "X-Mythlink-Native": "1", "Authorization": token}, method="POST")
    # Local traffic must never be sent to a proxy configured by the environment.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=timeout) as response:
        raw = response.read(MAX_MESSAGE + 1)
        if len(raw) > MAX_MESSAGE:
            raise ValueError("message_too_large")
        result = json.loads(raw)
        if not isinstance(result, dict):
            raise ValueError("invalid_reply")
        return result


def request(bundle, operation="snapshot", target=None, action=None):
    try:
        return post("/request", dict(bundle=bundle, operation=operation, target=target, action=action))
    except (OSError, ValueError):
        return {"error": "browser_disconnected"}
