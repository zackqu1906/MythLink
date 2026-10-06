"""Small observational records; no extra AX reads, logging I/O or UI calls."""
from dataclasses import replace


def explain(result, detector, reason, **facts):
    return replace(result, diagnostic={"detector": detector, "reason": reason, **facts})


def window_rejection(window, read, *, allow_dialog=False):
    if window is None:
        return "window_missing"
    if read(window, "AXRole") != "AXWindow":
        return "window_role_unavailable"
    if read(window, "AXModal"):
        return "modal_window"
    if read(window, "AXMinimized"):
        return "minimized_window"
    if read(window, "AXSheets"):
        return "sheet_open"
    subrole = read(window, "AXSubrole")
    if subrole == "AXSystemDialog" or (subrole == "AXDialog" and not allow_dialog):
        return "dialog_window"
    return ""
