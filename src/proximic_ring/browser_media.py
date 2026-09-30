"""Website-scoped video presets and bounded, read-only WebKit/AX evidence.

Only the focused page is inspected. URLs are retained as a hash, never logged;
DOM text, form values, cookies and JavaScript are not read or injected.
"""
from collections import deque
from dataclasses import dataclass, field
import hashlib
import ipaddress
import re
import time
from urllib.parse import urlsplit

from .scene_capabilities import VIDEO

BILIBILI = "bilibili.com"
VIDEO_ACTIONS = (("play", "播放 / 暂停", "Space"), ("backward", "后退", "Left"),
                 ("forward", "快进", "Right"), ("volume-up", "增大音量", "Up"),
                 ("volume-down", "减小音量", "Down"))


def website_domain(value):
    text = str(value or "").strip()
    if not text or len(text) > 2048:
        raise ValueError("请输入网站域名，例如 bilibili.com")
    parsed = urlsplit(text if "://" in text else "https://" + text)
    if parsed.scheme not in {"http", "https"} or parsed.username or parsed.password:
        raise ValueError("请使用 http 或 https 网站地址")
    host = (parsed.hostname or "").rstrip(".").encode("idna").decode().lower()
    try:
        ipaddress.ip_address(host)
    except ValueError:
        if not re.fullmatch(r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", host):
            raise ValueError("请输入完整的网站域名")
    if host == "www.bilibili.com":
        host = BILIBILI
    return host


def matches_website(host, domain):
    return bool(host and domain and (host == domain or host.endswith("." + domain)))


def video_actions(website=""):
    label = "哔哩哔哩播放器" if matches_website(website, BILIBILI) else "网页播放器（需支持对应按键）"
    return [dict(id="web-video:" + key, label=title, path=label, shortcut=shortcut,
                 preset=True, available=None) for key, title, shortcut in VIDEO_ACTIONS]


def website_defaults(website):
    if website != BILIBILI:
        return {}
    return {gesture: {key: action[key] for key in ("id", "label", "path", "shortcut")}
            for gesture, action in zip(("tap", "swipe-left", "swipe-right", "swipe-up", "swipe-down"), video_actions(website))}


@dataclass(frozen=True)
class BrowserMedia:
    website: str = ""
    page_key: str = ""
    web_area: object = field(default=None, repr=False)
    player: object = field(default=None, repr=False)
    scene: str = ""
    input_context: str = "unknown"


def browser_media_context(window, focus, attr, *, budget=.18):
    deadline = time.monotonic() + budget
    def read(node, key):
        if time.monotonic() >= deadline:
            raise TimeoutError()
        return attr(node, key) if node is not None else None
    try:
        if (window is None or read(window, "AXRole") != "AXWindow" or read(window, "AXModal")
                or read(window, "AXMinimized") or read(window, "AXSheets")
                or read(window, "AXSubrole") in {"AXDialog", "AXSystemDialog"}):
            return BrowserMedia()
        # The focused element must belong to this window and this page. An
        # address field, tab strip or sidebar is deliberately not a web target.
        node, web, ancestors, context = focus, None, [], "nontext"
        safe_roles = {"AXGroup", "AXWebArea", "AXScrollArea", "AXSplitGroup", "AXTabGroup", "AXWindow",
                      "AXButton", "AXCheckBox", "AXSlider", "AXImage", "AXLink", "AXStaticText"}
        for _ in range(24):
            if node is None:
                return BrowserMedia()
            ancestors.append(node)
            role = read(node, "AXRole")
            if role in {"AXTextField", "AXTextArea", "AXSearchField", "AXComboBox"} or read(node, "AXEditable") or read(node, "AXIsEditable"):
                context = "text"
            elif role not in safe_roles and context != "text":
                context = "unknown"
            if role == "AXWebArea":
                web = node  # Outermost page owns a nested frame's website.
                if read(node, "AXValueSettable") is not False and read(node, "AXEditable") is not False and read(node, "AXIsEditable") is not False and context != "text":
                    context = "unknown"
            if node == window:
                break
            node = read(node, "AXParent")
        if node != window or web is None or read(web, "AXHidden") or read(web, "AXElementBusy"):
            return BrowserMedia()
        url = str(read(web, "AXURL") or "")
        if urlsplit(url).scheme not in {"http", "https"}:
            return BrowserMedia()
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
            return BrowserMedia(**base, player=players[0], scene=VIDEO)
        return BrowserMedia(**base)
    except (TimeoutError, TypeError, ValueError, UnicodeError):
        return BrowserMedia()
