"""Recognize the focused webpage's real video player using metadata only."""
from collections import deque
import hashlib
import time
from urllib.parse import urlsplit

from .focus import inspect_focus
from .models import VIDEO, SceneResult
from .websites import BILIBILI, matches_website, website_domain


def detect_browser(window, focus, attr, *, budget=.18):
    deadline = time.monotonic() + budget
    def read(node, key):
        if time.monotonic() >= deadline:
            raise TimeoutError()
        return attr(node, key) if node is not None else None
    try:
        if (window is None or read(window, "AXRole") != "AXWindow" or read(window, "AXModal")
                or read(window, "AXMinimized") or read(window, "AXSheets")
                or read(window, "AXSubrole") in {"AXDialog", "AXSystemDialog"}):
            return SceneResult()
        # The focused element must belong to this window and this page. An
        # address field, tab strip or sidebar is deliberately not a web target.
        snapshot = inspect_focus(window, focus, read, window_reference=False)
        ancestors, context = snapshot.ancestors, snapshot.context
        web = next((node for node in reversed(ancestors) if read(node, "AXRole") == "AXWebArea"), None)
        if web is None or read(web, "AXElementBusy"):
            return SceneResult()
        url = str(read(web, "AXURL") or "")
        if urlsplit(url).scheme not in {"http", "https"}:
            return SceneResult()
        host = website_domain(url)
        page_key = hashlib.sha256(url.encode()).hexdigest()
        base = dict(website=host, page_key=page_key, web_area=web, input_context=context)
        queue, players, visited = deque([(web, 0, None)]), [], 0
        while queue and visited < 220:
            item, depth, bili = queue.popleft(); visited += 1
            if read(item, "AXHidden") or read(item, "AXEnabled") is False:
                continue
            role = read(item, "AXRole")
            if role == "AXWebArea" and item != web:
                continue  # Cross-origin frames require their own adapter.
            classes = read(item, "AXDOMClassList") or []
            if matches_website(host, BILIBILI) and "bpx-player-primary-area" in classes:
                bili = item
            if read(item, "AXSubrole") == "AXVideo" and read(item, "AXEnabled") is True:
                # Verified Bilibili player class + real video, or focus within a
                # generic native video. A page title or a thumbnail is not proof.
                if bili is not None or any(item == ancestor for ancestor in ancestors):
                    players.append(item)
                    if bili is not None and focus != web and not any(bili == ancestor for ancestor in ancestors) and context == "nontext":
                        # A focused follow/share button outside the player must
                        # not receive Space as an unintended button activation.
                        base["input_context"] = "unknown"
                continue
            if role in {"AXWebArea", "AXGroup", "AXScrollArea", "AXLayoutArea"} and depth < 10:
                children = list(read(item, "AXChildren") or [])
                # Depth first reaches the player's real video before unrelated
                # recommendations can exhaust the bounded scan.
                queue.extendleft((child, depth + 1, bili) for child in reversed(children[:220-visited]))
        if len(players) == 1:
            return SceneResult(**base, player=players[0], scene=VIDEO)
        return SceneResult(**base)
    except (TimeoutError, TypeError, ValueError, UnicodeError):
        return SceneResult()
