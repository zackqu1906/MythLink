"""Prepare browser AX metadata and batch structural reads in the native worker.

No scripting, focus changes, page text or global accessibility preferences.
The browser builds its tree asynchronously; normal scene polls discover it.
"""
from collections import OrderedDict
import time


class BrowserAccessibility:
    def __init__(self):
        self._processes = OrderedDict()

    def prepare(self, application, element, ax):
        launched = application.launchDate() if hasattr(application, 'launchDate') else None
        identity = (str(application.bundleIdentifier()), int(application.processIdentifier()),
                    float(launched.timeIntervalSince1970()) if launched else None)
        now = time.monotonic()
        previous = self._processes.get(identity)
        if previous and now < previous['retry_at']:
            return {**previous['diagnostic'], 'warming': now < previous['ready_at']}
        setter = getattr(ax, 'AXUIElementSetAttributeValue', None)
        if setter is None:
            return {'state': 'api_unavailable', 'warming': False}
        # Chromium watches this application attribute and debounces activation
        # for two seconds. Rewriting it on every 400ms poll prevents activation.
        # Safari exposes the same AppKit attribute. A NotImplemented return can
        # still have enabled it; tree evidence, never this flag, proves a scene.
        attribute = 'AXEnhancedUserInterface'
        error, enabled = ax.AXUIElementCopyAttributeValue(element, attribute, None)
        diagnostic = {'attribute': attribute, 'read_error': int(error)}
        if not error and enabled:
            diagnostic['state'] = 'already_enabled'
            ready_at, retry_at = now, float('inf')
        elif error not in (0, -25205, -25212):
            diagnostic['state'] = 'unavailable'
            ready_at, retry_at = now, now + 5.0
        else:
            result = setter(element, attribute, True)
            diagnostic.update(state='requested', request_error=int(result))
            ready_at = now + 2.2
            retry_at = float('inf') if result in (0, -25208, -25205) else now + 5.0
            if result not in (0, -25208):
                diagnostic['state'] = 'unsupported' if result == -25205 else 'unavailable'
                ready_at = now
        self._processes[identity] = dict(diagnostic=diagnostic, ready_at=ready_at, retry_at=retry_at)
        self._processes.move_to_end(identity)
        while len(self._processes) > 64:
            self._processes.popitem(last=False)
        return {**diagnostic, 'warming': now < ready_at}


# These are structural metadata only. Titles/labels are read selectively by
# PlaybackControls; values, selected text and page titles never enter a batch.
STRUCTURE_ATTRIBUTES = ('AXRole', 'AXSubrole', 'AXHidden', 'AXEnabled',
                        'AXDOMClassList', 'AXDOMIdentifier', 'AXChildren')


def read_structure(ax, node):
    """Return per-attribute (error, value), or None for the single-read fallback."""
    copy = getattr(ax, 'AXUIElementCopyMultipleAttributeValues', None)
    if copy is None:
        return None
    error, values = copy(node, STRUCTURE_ATTRIBUTES, 0, None)
    if error or values is None or len(values) != len(STRUCTURE_ATTRIBUTES):
        return None
    result = {}
    for name, value in zip(STRUCTURE_ATTRIBUTES, values):
        # Missing attributes arrive as AXValue(kAXValueAXErrorType), not None.
        # Never let a truthy error wrapper masquerade as AXHidden / AXEnabled.
        if isinstance(value, ax.AXValueRef):
            if ax.AXValueGetType(value) != ax.kAXValueAXErrorType:
                return None
            valid, code = ax.AXValueGetValue(value, ax.kAXValueAXErrorType, None)
            if not valid:
                return None
            result[name] = (int(code), None)
        else:
            result[name] = (0, value)
    return result
