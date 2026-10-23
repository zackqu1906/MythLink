"""Explicit setup check: one external window, one frame, no retained content."""
import os
import threading
import time

from .window_capture import WindowCapture
from .window_selector import MacWindowAX, area
from .text_focus import intersection


def preview_check_target():
    """Use a normal window on the settings window's screen; never a display stream."""
    ax = MacWindowAX()
    rows, apps, screens = ax.on_screen(), ax.apps(), ax.screens()
    own = next((row for row in rows if row["pid"] == os.getpid()), None)
    if own is None or not screens:
        return None
    screen = max(screens, key=lambda s: area(intersection(s["frame"], own["frame"])))
    for row in rows:
        if row["pid"] == os.getpid() or row["pid"] not in apps:
            continue
        if row["frame"][2] < 160 or row["frame"][3] < 100:
            continue
        belonging = max(screens, key=lambda s: area(intersection(s["frame"], row["frame"])))
        if belonging["id"] == screen["id"]:
            return {"id": "permission-check", "pid": row["pid"],
                    "number": row["number"], "frame": row["frame"]}
    return None


def verify_screen_preview(cancel, *, target_reader=preview_check_target,
                          factory=WindowCapture, timeout=45):
    """Do not infer picker-bypass consent from the basic TCC preflight flag."""
    engine = None
    done = threading.Event()
    lock = threading.Lock()
    outcome = "timeout"

    def finish(result):
        nonlocal outcome
        with lock:
            if done.is_set() or cancel.is_set():
                return
            outcome = result
            done.set()
        engine.stop()  # Drop subsequent frames before reporting to the UI.

    try:
        if cancel.is_set():
            return "cancelled"
        engine = factory()
        if not engine.permission():
            return "permission"
        target = target_reader()
        if target is None:
            return "no_window"
        if cancel.is_set():
            return "cancelled"
        def frame(key, image):
            if not image.isNull():
                finish("verified")
        def status(value):
            if value in {"permission", "unavailable"}:
                finish(value)
        engine.start([target], frame, status)
        deadline = time.monotonic() + timeout
        while not done.wait(.05):
            if cancel.is_set() or time.monotonic() >= deadline:
                break
        return "cancelled" if cancel.is_set() else outcome
    except Exception:
        return "unavailable"
    finally:
        if engine is not None:
            engine.close()
