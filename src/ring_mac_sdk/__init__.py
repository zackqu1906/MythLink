"""R1 macOS SDK. All asynchronous objects belong to one asyncio event loop."""
from .errors import ProtocolError, RingError
from .ring import Ring
from .transport import DiscoveredRing, scan
from .types import (Click, ContactRange, ContactUpdate, DeviceInfo, InfoComponent, EventMeta,
                    GestureEvent, GestureMode, GestureResponse, Motion,
                    SwipeDirection, TouchpadEvent, TouchpadStatus, WearingHand)

__version__ = "0.3.0"
__all__ = ["Ring", "scan", "DiscoveredRing", "RingError", "ProtocolError", "GestureMode",
           "WearingHand", "SwipeDirection", "GestureEvent", "GestureResponse", "DeviceInfo", "InfoComponent",
           "Motion", "ContactUpdate", "ContactRange", "Click", "EventMeta", "TouchpadEvent", "TouchpadStatus"]
