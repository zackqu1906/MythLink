"""Bounded first-gesture IMK focus checks; no text commands."""
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
    native_target: object = None


def probe_input_focus(bridge, foreground, select_source, *, timeout=0.8, native_fallback=None,
                      activation_focus=None, verify_activation_target=None):
    """Run on the gesture worker. Source selection and replies share a deadline.

    A bound client's invalid selection is nontext. Outside an explicitly
    supplied native fallback, an absent client or timeout stays unavailable.
    The fallback may allow unknown focus in a confirmed presentation; its
    exact native window and scene must also be revalidated before dispatch.
    """
    started = time.monotonic()
    deadline = started + timeout
    origin = foreground()
    epoch = bridge.epoch

    def result(status, reason="", state=None, native_target=None):
        target = None
        if state is not None:
            target = (*(str(state.get(key) or "") for key in
                        ("epoch", "client_id", "utterance_id", "application")),
                      origin[1], tuple(state["selection"]))
        elif native_target is not None:
            target = (epoch, "", "", origin[0], origin[1], ())
        return InputFocus(status, target, reason, round((time.monotonic() - started) * 1000, 2),
                          native_target)

    def changed():
        return foreground() != origin or bool(epoch and bridge.epoch != epoch)

    native_checked, native_target = False, None

    def native_focus():
        nonlocal native_checked, native_target
        if native_fallback is None:
            return None
        if not native_checked:
            native_checked = True
            native_target = native_fallback(origin)
        if foreground() != origin:
            return result("unavailable", "foreground_or_connection_changed")
        if native_target is not None:
            return result("outside", "passive_nontext_focus" if select_source is None else "presentation_focus_fallback",
                          native_target=native_target)
        return None

    def unavailable(reason):
        return native_focus() or result("unavailable", reason)

    if not origin or origin[1] <= 0:
        return result("unavailable", "target_unavailable")
    selected = False
    wechat_target = None
    try:
        while time.monotonic() < deadline:
            if not epoch:
                epoch = bridge.epoch
            if changed():
                return unavailable("foreground_or_connection_changed")
            connected = bridge.connected
            state = bridge.focus_state(timeout=min(.2, max(0, deadline - time.monotonic()))) if connected else None
            if changed():
                return unavailable("foreground_or_connection_changed")
            if time.monotonic() >= deadline:
                break
            if state and state.get("application") == origin[0] and state.get("client_id"):
                if state.get("ready") and valid_selection(state.get("selection")):
                    if wechat_target is not None:
                        # A valid IMK client in a different WeChat window cannot authorize this tap.
                        if not verify_activation_target(wechat_target) or changed():
                            return result("unavailable", "foreground_or_connection_changed")
                        if time.monotonic() >= deadline:
                            return result("unavailable", "input_method_timeout")
                    return result("text", state=state)
                if absent_selection(state.get("selection")):
                    if select_source is None:
                        return result("nontext", "input_method_no_focus", state=state)
                    return native_focus() or result("nontext", "input_method_no_focus", state=state)
                if valid_selection(state.get("selection")):
                    # A known editor that is not ready still owns this gesture.
                    return result("unavailable", "input_method_not_ready")
                return unavailable("input_method_not_ready")
            if connected and state is None and not selected:
                return unavailable("input_method_timeout")
            if not selected:
                if select_source is not None and activation_focus is not None:
                    # The IMK client is inactive. Only a currently editable control can justify borrowing a keyboard.
                    target = activation_focus(origin)
                    if changed():
                        return result("unavailable", "foreground_or_connection_changed")
                    if target is None or target.blocked or (target.bundle, target.pid) != origin:
                        return unavailable("unknown_focus")
                    if target.input_context == "nontext":
                        return result("outside", "passive_nontext_focus", native_target=target)
                    if target.input_context != "text":
                        if (target.input_context == "unknown" and target.wechat_ime_probe
                                and origin[0] in {"com.tencent.xinWeChat", "com.tencent.WeChat"}
                                and verify_activation_target is not None):
                            wechat_target = target
                        else:
                            return unavailable("unknown_focus")
                else:
                    native = native_focus()
                    if native is not None:
                        return native
                if changed():
                    return unavailable("foreground_or_connection_changed")
                if time.monotonic() >= deadline:
                    break
                if select_source is None:
                    return result("unavailable", "unknown_focus")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                if wechat_target is not None:
                    select_source(origin[1], timeout=remaining, wechat_probe=True)
                else:
                    select_source(origin[1], timeout=remaining)
                selected = True
            else:
                time.sleep(min(.02, max(0, deadline - time.monotonic())))
    except TimeoutError:
        return unavailable("input_method_timeout")
    except Exception as exc:
        if changed():
            return unavailable("foreground_or_connection_changed")
        reason = getattr(exc, 'reason', '')
        if reason in {'input_method_source_shortcut_unavailable',
                      'input_method_source_event_permission', 'input_method_source_modifiers_held'}:
            return unavailable(reason)
        return unavailable("input_method_activation_failed")
    return unavailable("input_method_timeout")


def same_input_focus(bridge, foreground, proof, *, timeout=.2):
    """Revalidate from correlated selection metadata without activating a source."""
    target = proof.target if proof else None
    if not target or len(target) != 6 or (not bridge and proof.native_target is None):
        return False
    origin = (target[3], target[4])
    started = time.monotonic()
    if proof.native_target is not None:
        # In a confirmed show, uncertainty is allowed. Only positive current
        # text focus vetoes the action; native dispatch still checks the show.
        if foreground() != origin:
            return False
        try:
            state = bridge.focus_state(timeout=timeout) if bridge and bridge.connected else None
        except Exception:
            state = None
        return bool(proof.status == "outside" and foreground() == origin
                    and not (state and state.get("application") == origin[0]
                             and state.get("client_id") and valid_selection(state.get("selection"))))
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
