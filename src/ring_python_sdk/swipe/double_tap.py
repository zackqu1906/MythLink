"""Compose confirmed firmware taps without invoking application actions."""

from ring_python_sdk.swipe.double_pinch import DoublePinchDetector, DoublePinchEvent


# Both compositions carry the same immutable pair of firmware triggers.
DoubleTapEvent = DoublePinchEvent


class DoubleTapDetector(DoublePinchDetector):
    """Emit ``double-tap`` on the second confirmed tap, using device time.

    Feed every SwipeResult from one ring, including other gestures, serially.
    Singles never emit; classifications and duplicate packets never form pairs.
    The interval is inclusive and pairs do not overlap. Other gestures, missing
    triggers and clock/protocol changes cancel a pending pair. Call reset() on
    disconnect or when starting a new capture. Firmware must detect both taps.
    """

    def __init__(self, *, min_interval_ms: float = 100, max_interval_ms: float = 200):
        super().__init__(pinch_name="tap", min_interval_ms=min_interval_ms,
                         max_interval_ms=max_interval_ms)
