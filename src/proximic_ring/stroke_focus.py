"""Read-only eligibility check for the already focused stroke input target."""
from .text_focus import MacAX, TEXT_ROLES


def editable_at_point(point, ax=None, *, node=None):
    """Hit-test only the click location; never search for another input field."""
    ax = ax or MacAX()
    node = editable_node_at_point(point, ax) if node is None else node
    if node is not None:
        error, pid = ax.ax.AXUIElementGetPid(node, None)
        return dict(bundle=ax.bundle(pid), pid=pid) if error == 0 else None
    return None


def editable_node_at_point(point, ax=None):
    ax = ax or MacAX()
    node = ax.hit_test(point)
    for _ in range(6):
        if node is None:
            return None
        meta = ax.metadata(node)
        if (meta.get("AXEnabled") is False or meta.get("AXHidden")
                or meta.get("AXSubrole") == "AXSecureTextField"):
            return None
        if (meta.get("AXIsEditable") is not False
                and (meta.get("AXRole") in TEXT_ROLES or meta.get("AXIsEditable") is True)
                and (meta.get("AXIsEditable") is True or ax.settable(node, "AXValue"))):
            return node
        # Web editors can expose static text as the leaf under the pointer.
        if meta.get("AXRole") not in {"AXStaticText", "AXText", "AXGroup", "AXUnknown"}:
            return None
        node = ax.attr(node, "AXParent")
    return None


def point_matches_focus(point, target, ax=None):
    ax = ax or MacAX()
    pointed = editable_node_at_point(point, ax)
    focus = target.focus
    for _ in range(6):
        if focus is None or pointed is None: return False
        if focus == pointed: return True
        focus = ax.attr(focus, "AXParent")
    return False


def editable_target(target, ax=None):
    if target.focus is None or target.window is None or target.blocked:
        return False
    ax = ax or MacAX()
    meta = ax.metadata(target.focus)
    if (meta.get("AXEnabled") is False or meta.get("AXHidden")
            or meta.get("AXIsEditable") is False
            or meta.get("AXSubrole") == "AXSecureTextField"
            or ax.attr(target.focus, "AXFocused") is False):
        return False
    return (meta.get("AXRole") in TEXT_ROLES or meta.get("AXIsEditable") is True) and (
        meta.get("AXIsEditable") is True or ax.settable(target.focus, "AXValue"))
