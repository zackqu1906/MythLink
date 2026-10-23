"""Process-local App Nap protection for explicitly active input sessions."""
from __future__ import annotations

import logging
import sys
import threading

log = logging.getLogger(__name__)


class MacActivity:
    """Own one balanced activity; start/close are idempotent, never per frame.

    UserInitiatedAllowingIdleSystemSleep keeps user input active in the
    background without preventing normal system sleep. The latency-sensitive
    scopes additionally request timer/I/O precision. Like WebKit's
    UserActivityMac.mm, leave termination policy to the application.
    """
    def __init__(self, reason: str, *, latency_critical: bool = False):
        self.reason = reason
        self.latency_critical = latency_critical
        self._token = self._process = None
        self._lock = threading.Lock()

    @property
    def active(self):
        return self._token is not None

    def start(self):
        if sys.platform != 'darwin':
            return False
        with self._lock:
            if self._token is not None:
                return True
            try:
                import Foundation as f
                options = f.NSActivityUserInitiatedAllowingIdleSystemSleep
                options &= ~(f.NSActivitySuddenTerminationDisabled |
                             f.NSActivityAutomaticTerminationDisabled)
                if self.latency_critical:
                    options |= f.NSActivityLatencyCritical
                process = f.NSProcessInfo.processInfo()
                token = process.beginActivityWithOptions_reason_(options, self.reason)
                if token is None:
                    raise RuntimeError('macOS did not return an activity token')
                self._process, self._token = process, token
                log.info('[APP_ACTIVITY] state=began latency_critical=%s reason=%r',
                         self.latency_critical, self.reason)
                return True
            except Exception:
                # Unsupported protection must be visible, without disabling
                # otherwise working input or requesting unrelated permissions.
                log.warning('[APP_ACTIVITY] state=failed reason=%r', self.reason, exc_info=True)
                return False

    def close(self):
        with self._lock:
            if self._token is None:
                return
            try:
                self._process.endActivity_(self._token)
                log.info('[APP_ACTIVITY] state=ended latency_critical=%s reason=%r',
                         self.latency_critical, self.reason)
            except Exception:
                log.warning('[APP_ACTIVITY] state=end_failed reason=%r', self.reason, exc_info=True)
            finally:
                self._token = self._process = None
