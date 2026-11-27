from PySide6.QtGui import QGuiApplication


def screen_caret(caret):
    if len(caret) != 4:
        return None
    from ...voice.ime.windows_bridge import caret_monitor_origin

    # Windows reports physical pixels. Qt scales each monitor about its own origin.
    origin = caret_monitor_origin(caret)
    screen = next((s for s in QGuiApplication.screens()
                   if (s.geometry().x(), s.geometry().y()) == origin), QGuiApplication.primaryScreen())
    if screen is None:
        return None
    x, y = screen.geometry().x(), screen.geometry().y()
    logical = [base + (value - base) / screen.devicePixelRatio()
               for value, base in zip(caret, (x, y, x, y))]
    return logical, screen.availableGeometry()
