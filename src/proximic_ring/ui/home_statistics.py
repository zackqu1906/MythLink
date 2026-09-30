"""Read-only statistics over the existing, unfiltered voice history projection."""
from datetime import datetime, timedelta
import math

from PySide6.QtCore import QObject, Property, QTimer, Signal, Slot


def _empty_totals():
    return dict(records=0, inputCharacters=0, completedOperations=0, voiceSeconds=0.0,
                missingText=0, missingDuration=0)


def summarize_history(entries, *, now=None):
    now = now or datetime.now()
    today = now.date()
    yesterday = today - timedelta(days=1)
    result = dict(day=today.isoformat(), today=_empty_totals(), yesterday=_empty_totals(),
                  total=_empty_totals(), undatedRecords=0)
    seen = set()
    for entry in entries:
        identity = entry.get("interactionId") or entry.get("id")
        if not identity or identity in seen:
            continue
        seen.add(identity)
        try:
            timestamp = datetime.fromisoformat(str(entry.get("createdAt") or ""))
            # Legacy naive timestamps are local. Aware timestamps use the OS's
            # date-specific local offset (or the explicitly supplied test zone).
            day = (timestamp.astimezone(now.tzinfo) if timestamp.tzinfo else timestamp).date()
        except (ValueError, OverflowError, OSError):
            day = None
            result["undatedRecords"] += 1
        contribution = _empty_totals()
        contribution["records"] = 1
        try:
            duration = float(entry.get("durationSeconds"))
            if not math.isfinite(duration) or duration < 0:
                raise ValueError()
            contribution["voiceSeconds"] = duration
        except (TypeError, ValueError, OverflowError):
            contribution["missingDuration"] = 1
        if entry.get("outcome") in {"applied", "confirm"} and entry.get("mode") in {"dictation", "edit"}:
            contribution["completedOperations"] = 1
            if entry["mode"] == "dictation":
                text = entry.get("candidateText")
                if entry.get("candidateAvailable") is True and isinstance(text, str):
                    contribution["inputCharacters"] = sum(not char.isspace() for char in text)
                else:
                    # An ASR transcript is not evidence of text actually entered.
                    contribution["missingText"] = 1
        buckets = [result["total"]]
        if day == today:
            buckets.append(result["today"])
        elif day == yesterday:
            buckets.append(result["yesterday"])
        for bucket in buckets:
            for key, value in contribution.items():
                bucket[key] += value
    for name in ("today", "yesterday", "total"):
        result[name]["voiceSeconds"] = round(result[name]["voiceSeconds"], 3)
    return result


class HomeStatistics(QObject):
    changed = Signal()
    activeChanged = Signal()

    def __init__(self, entries, parent=None, *, clock=datetime.now):
        super().__init__(parent)
        self._entries = entries
        self._clock = clock
        self._active = False
        self._dirty = True
        self._closed = False
        self._calendar = None
        self._summary = summarize_history([], now=clock())
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.setInterval(100)
        self._refresh_timer.timeout.connect(self._refresh)
        self._date_timer = QTimer(self)
        self._date_timer.setInterval(60_000)
        self._date_timer.timeout.connect(self.refreshDate)

    @Property("QVariantMap", notify=changed)
    def summary(self):
        return {key: dict(value) if isinstance(value, dict) else value for key, value in self._summary.items()}

    @Property(bool, notify=activeChanged)
    def active(self):
        return self._active

    @active.setter
    def active(self, value):
        value = bool(value) and not self._closed
        if value == self._active:
            return
        self._active = value
        if value:
            self.refreshDate()
            self._date_timer.start()
        else:
            self._date_timer.stop()
            self._refresh_timer.stop()
        self.activeChanged.emit()

    @Slot()
    def historyChanged(self):
        self._dirty = True
        if self._active and not self._refresh_timer.isActive():
            self._refresh_timer.start()

    @Slot()
    def refreshDate(self):
        if not self._active or self._closed:
            return
        now = self._clock()
        if self._dirty or self._calendar != self._calendar_key(now):
            self._refresh(now)

    @staticmethod
    def _calendar_key(now):
        return now.date(), now.utcoffset() if now.tzinfo else now.astimezone().utcoffset()

    def _refresh(self, now=None):
        if not self._active or self._closed:
            return
        self._refresh_timer.stop()
        now = now or self._clock()
        updated = summarize_history(self._entries(), now=now)
        self._calendar = self._calendar_key(now)
        self._dirty = False
        if updated != self._summary:
            self._summary = updated
            self.changed.emit()

    def close(self):
        self._closed = True
        self.active = False
