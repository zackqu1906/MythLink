"""Bounded first-gesture IMK focus checks; no AX or text commands."""
from dataclasses import dataclass
import time


def valid_selection(selection):
    return (isinstance(selection, (list, tuple)) and len(selection) == 2
            and all(type(value) is int and 0 <= value < (1 << 63) - 1 for value in selection)
            and sum(selection) < (1 << 63) - 1)


def absent_selection(selection):
    # This is the native bridge's explicit NSNotFound representation. A
    # missing or malformed field is a protocol failure, not evidence of blur.
    return isinstance(selection, (list, tuple)) and list(selection) == [-1, -1]


@dataclass(frozen=True)
class InputFocus:
    status: str
    target: tuple | None = None
    reason: str = ""
    elapsed_ms: float = 0


def probe_input_focus(bridge, foreground, select_source, *, timeout=0.8):
    """Run on the gesture worker. Source selection and replies share a deadline.

    A bound client's invalid selection is nontext. An absent client or a
    timeout is unavailable, never permission to start or run an app shortcut.
    """
    started = time.monotonic()
    deadline = started + timeout
    origin = foreground()
    epoch = bridge.epoch

    def result(status, reason="", state=None):
        target = None
        if state is not None:
            target = (*(str(state.get(key) or "") for key in
                        ("epoch", "client_id", "utterance_id", "application")),
                      origin[1], tuple(state["selection"]))
        return InputFocus(status, target, reason, round((time.monotonic() - started) * 1000, 2))

    def changed():
        return foreground() != origin or bool(epoch and bridge.epoch != epoch)

    if not origin or origin[1] <= 0:
        return result("unavailable", "target_unavailable")
    selected = False
    try:
        while time.monotonic() < deadline:
            if not epoch:
                epoch = bridge.epoch
            if changed():
                return result("unavailable", "foreground_or_connection_changed")
            connected = bridge.connected
            state = bridge.focus_state(timeout=min(.2, max(0, deadline - time.monotonic()))) if connected else None
            if changed():
                return result("unavailable", "foreground_or_connection_changed")
            if time.monotonic() >= deadline:
                break
            if state and state.get("application") == origin[0] and state.get("client_id"):
                if state.get("ready") and valid_selection(state.get("selection")):
                    return result("text", state=state)
                if absent_selection(state.get("selection")):
                    return result("nontext", "input_method_no_focus", state=state)
                return result("unavailable", "input_method_not_ready")
            if connected and state is None and not selected:
                return result("unavailable", "input_method_timeout")
            if not selected:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                select_source(origin[1], timeout=remaining)
                selected = True
            else:
                time.sleep(min(.02, max(0, deadline - time.monotonic())))
    except TimeoutError:
        return result("unavailable", "input_method_timeout")
    except Exception as exc:
        if changed():
            return result("unavailable", "foreground_or_connection_changed")
        reason = getattr(exc, 'reason', '')
        if reason in {'input_method_source_shortcut_unavailable',
                      'input_method_source_event_permission', 'input_method_source_modifiers_held'}:
            return result('unavailable', reason)
        return result("unavailable", "input_method_activation_failed")
    return result("unavailable", "input_method_timeout")


def same_input_focus(bridge, foreground, proof, *, timeout=.2):
    """Revalidate from correlated selection metadata without activating a source."""
    target = proof.target if proof else None
    if not target or len(target) != 6 or not bridge:
        return False
    origin = (target[3], target[4])
    started = time.monotonic()
    try:
        if foreground() != origin or bridge.epoch != target[0]:
            return False
        state = bridge.focus_state(timeout=timeout)
        if (not state or time.monotonic() - started >= timeout
                or foreground() != origin or bridge.epoch != target[0]
                or tuple(str(state.get(key) or "") for key in
                         ("epoch", "client_id", "utterance_id", "application")) != target[:4]):
            return False
        selection = state.get("selection")
        if proof.status == "nontext":
            return absent_selection(selection)
        return bool(proof.status == "text" and state.get("ready")
                    and valid_selection(selection) and tuple(selection) == target[5])
    except Exception:
        return False
