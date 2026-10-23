"""Browser scenes come exclusively from the authorized WebExtension.

No AX tree, labels, URLs, site whitelist or keyboard heuristics are used here.
Native window guards belong to the desktop boundary, outside this adapter.
"""
from ...browser_bridge.client import request
from ...browser_bridge.protocol import BUNDLES, clean_snapshot
from ..models import SceneResult


def detect_browser(bundle, *, profiles, snapshot=None):
    if bundle.casefold() not in {b.casefold() for b in BUNDLES.values()}:
        return SceneResult(diagnostic={"reason": "browser_unsupported", "source": "extension"})
    value = request(bundle) if snapshot is None else snapshot
    if "error" in value:
        return SceneResult(diagnostic={"reason": value["error"], "source": "extension"})
    try:
        value = clean_snapshot(value)
    except (ValueError, TypeError):
        return SceneResult(diagnostic={"reason": "browser_page_unavailable", "source": "extension"})
    reason = value["reason"]
    scene = value["scene"] if value["focused"] and value["authorized"] and value["scene"] in profiles else ""
    if not value["focused"]:
        reason = "browser_window_unfocused"
    elif not value["authorized"]:
        reason = "browser_permission_required"
    page = ":".join(str(value[k]) for k in ("session", "window", "tab", "page")) if value["page"] else ""
    player = ":".join(str(value[k]) for k in ("frame", "document", "media", "source")) if scene else None
    context = value["input_context"] if value["focused"] and value["authorized"] else "unknown"
    return SceneResult(scene=scene, input_context=context, page_key=page,
        web_area=page or None, player=player, browser_target=value,
        diagnostic={"reason": reason, "source": "extension", "authorized": value["authorized"]})
