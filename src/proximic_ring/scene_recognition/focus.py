"""Shared metadata-only focus boundary for native scenes and browser players."""
from dataclasses import dataclass, field

TEXT_ROLES = {"AXTextField", "AXTextArea", "AXSearchField", "AXComboBox"}
SAFE_ROLES = {"AXWindow", "AXButton", "AXImage", "AXGroup", "AXLayoutArea", "AXScrollArea",
              "AXStaticText", "AXToolbar", "AXSplitGroup", "AXSlider", "AXValueIndicator",
              "AXTable", "AXRow", "AXList", "AXOutline", "AXCheckBox", "AXRadioButton",
              "AXTabGroup", "AXLink"}


@dataclass(frozen=True)
class FocusSnapshot:
    context: str = "unknown"
    ancestors: tuple = field(default=(), repr=False)
    blocked: bool = False


def inspect_focus(window, focus, read, *, window_reference=True):
    """Classify only a live focus belonging to this window, including ancestors.

    Missing optional flags are tolerated. Explicit hidden/disabled/stale focus
    and menu/dialog ancestors are rejected; text alone does not prove ownership.
    Native canvases may expose AXWindow when AXParent is unavailable.
    """
    if window is None or focus is None:
        return FocusSnapshot()
    node, ancestors, context = focus, [], "nontext"
    for _ in range(24):
        if node is None or any(node is previous for previous in ancestors):
            return FocusSnapshot()
        ancestors.append(node)
        role = read(node, "AXRole")
        if (read(node, "AXHidden") or read(node, "AXEnabled") is False
                or read(node, "AXElementBusy")
                or (node is focus and read(node, "AXFocused") is False)
                or role in {"AXMenu", "AXMenuItem", "AXMenuBar", "AXSheet", "AXDialog"}
                or (node != window and read(node, "AXSubrole") in {"AXDialog", "AXSystemDialog"})
                or (role == "AXWindow" and node != window)):
            return FocusSnapshot(blocked=True)
        editable, is_editable = read(node, "AXEditable"), read(node, "AXIsEditable")
        if role in TEXT_ROLES or editable or is_editable:
            context = "text"
        elif context != "text":
            if role == "AXWebArea":
                if editable is not False and is_editable is not False and read(node, "AXValueSettable") is not False:
                    context = "unknown"
            elif role not in SAFE_ROLES:
                context = "unknown"
        if node == window:
            return FocusSnapshot(context, tuple(ancestors))
        parent = read(node, "AXParent")
        if parent is None and window_reference and role != "AXWebArea" and read(node, "AXWindow") == window:
            parent = window
        node = parent
    return FocusSnapshot()
