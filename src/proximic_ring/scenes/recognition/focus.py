"""Shared metadata-only focus boundary for native scenes and browser players."""
from dataclasses import dataclass, field

TEXT_ROLES = {"AXTextField", "AXTextArea", "AXSearchField", "AXComboBox"}
SAFE_ROLES = {"AXWindow", "AXButton", "AXImage", "AXGroup", "AXLayoutArea", "AXScrollArea",
              "AXStaticText", "AXToolbar", "AXSplitGroup", "AXSlider", "AXValueIndicator",
              "AXTable", "AXRow", "AXList", "AXOutline", "AXGrid", "AXCheckBox", "AXRadioButton",
              "AXTabGroup", "AXLink"}


# These roles describe layout/content containers, not a text or button action.
# Their AXEnabled flag is not the enabled state of a window-level shortcut.
# Never include AXWindow, AXWebArea, or interactive controls in this policy.
WINDOW_SHORTCUT_CONTAINERS = frozenset({
    "AXLayoutArea", "AXGroup", "AXSplitGroup", "AXScrollArea", "AXToolbar",
    "AXImage", "AXStaticText",
})


@dataclass(frozen=True)
class FocusSnapshot:
    context: str = "unknown"
    ancestors: tuple = field(default=(), repr=False)
    blocked: bool = False
    reason: str = field(default="", compare=False)
    disabled_containers: tuple = field(default=(), compare=False)
    unfocused_containers: tuple = field(default=(), compare=False)


def inspect_focus(window, focus, read, *, window_reference=True, window_shortcut=False, page_focus=False):
    """Classify only a live focus belonging to this window, including ancestors.

    Missing optional flags are tolerated. Explicit hidden/disabled/stale focus
    and menu/dialog ancestors are rejected; text alone does not prove ownership.
    Native canvases may expose AXWindow when AXParent is unavailable.

    Window-level application shortcuts may tolerate disabled/unfocused layout containers,
    only after checking their full ownership/ancestor chain and editability.
    A browser can return its active AXWebArea as AXFocusedUIElement while the
    area's own AXFocused is false after a tab switch. Window commands do not
    require the page itself to be the keyboard responder. This hint is advisory
    only after proving its window ownership and checking all blocking ancestors.
    The browser adapter may opt into page_focus for this same web-root hint,
    without tolerating disabled containers. Text/focused-control operations keep
    strict defaults; editable web roots and incomplete ownership still fail.
    """
    # An application command may run with no document window. Still inspect
    # any reported focus and its ancestors for menus, dialogs and stale nodes.
    if focus is None or (window is None and not window_shortcut):
        return FocusSnapshot(reason="focus_missing")
    node, ancestors, context = focus, [], "nontext"
    reason = "nontext"
    disabled_containers, unfocused_containers = [], []
    for _ in range(24):
        if node is None or any(node is previous for previous in ancestors):
            return FocusSnapshot(blocked=bool(disabled_containers or unfocused_containers), reason="focus_chain_incomplete")
        ancestors.append(node)
        role = read(node, "AXRole")
        if read(node, "AXHidden"):
            return FocusSnapshot(blocked=True, reason="hidden_focus")
        disabled_container = read(node, "AXEnabled") is False
        if disabled_container and not (window_shortcut and role in WINDOW_SHORTCUT_CONTAINERS):
            return FocusSnapshot(blocked=True, reason="disabled_focus")
        if read(node, "AXElementBusy"):
            return FocusSnapshot(blocked=True, reason="busy_focus")
        unfocused_container = False
        if node is focus and read(node, "AXFocused") is False:
            # AXFocusedUIElement may be a document's noninteractive canvas.
            # Its own AXFocused flag need not be true for window/menu commands.
            # As with disabled containers, accept only after proving ownership
            # and excluding editable nodes and all blocking ancestors below.
            if not ((window_shortcut and role in WINDOW_SHORTCUT_CONTAINERS)
                    or ((window_shortcut or page_focus) and role == "AXWebArea")):
                return FocusSnapshot(blocked=True, reason="stale_focus")
            unfocused_container = True
        if role in {"AXMenu", "AXMenuItem", "AXMenuBar", "AXSheet", "AXDialog"}:
            return FocusSnapshot(blocked=True, reason="menu_or_sheet_focus")
        if node != window and read(node, "AXSubrole") in {"AXDialog", "AXSystemDialog"}:
            return FocusSnapshot(blocked=True, reason="dialog_focus")
        if window is None and role == "AXApplication":
            return FocusSnapshot(reason="application_focus")
        if role == "AXWindow" and node != window:
            return FocusSnapshot(blocked=True, reason="foreign_window_focus")
        editable, is_editable = read(node, "AXEditable"), read(node, "AXIsEditable")
        if unfocused_container:
            if editable or is_editable:
                return FocusSnapshot(blocked=True, reason="stale_focus")
            unfocused_containers.append(role)
        if disabled_container:
            if editable or is_editable:
                return FocusSnapshot(blocked=True, reason="disabled_focus")
            disabled_containers.append(role)
        if role in TEXT_ROLES or editable or is_editable:
            context, reason = "text", "text_focus"
        elif context != "text":
            if role == "AXWebArea":
                if editable is not False and is_editable is not False and read(node, "AXValueSettable") is not False:
                    context, reason = "unknown", "web_editability_unknown"
            elif role not in SAFE_ROLES:
                context, reason = "unknown", "unknown_focus_role"
        if node == window:
            return FocusSnapshot(context, tuple(ancestors), reason=reason,
                                 disabled_containers=tuple(disabled_containers),
                                 unfocused_containers=tuple(unfocused_containers))
        parent = read(node, "AXParent")
        if parent is None and window_reference and role != "AXWebArea" and read(node, "AXWindow") == window:
            parent = window
        node = parent
    return FocusSnapshot(blocked=bool(disabled_containers or unfocused_containers), reason="focus_depth_limit")
