"""Presentation-only search and filters over the live history projection."""
from datetime import datetime, timedelta

from PySide6.QtCore import Property, QSortFilterProxyModel, Signal, Slot, QTimer, Qt
from ..desktop.application_identity import application_presentation


class VoiceHistoryFilter(QSortFilterProxyModel):
    changed = Signal()
    applicationsChanged = Signal()
    countChanged = Signal()
    ENTRY_ROLE = int(Qt.ItemDataRole.UserRole) + 1
    OUTCOMES = {
        "completed": {"applied", "confirm"},
        "undone": {"undone", "native_undo_sent"},
        "failed": {"apply_failed", "abandoned"},
        "cancelled": {"cancelled", "cancel"},
        "unrecognized": {"unrecognized"},
        "interrupted": {"interrupted"},
    }

    def __init__(self, source, parent=None):
        super().__init__(parent)
        self._search = ""
        self._mode = "all"
        self._outcome = "all"
        self._application = "all"
        self._time = "all"
        self._applications = []
        self._terms = []
        self.setFilterRole(self.ENTRY_ROLE)
        self.setSourceModel(source)
        self.rowsInserted.connect(self.countChanged)
        self.rowsRemoved.connect(self.countChanged)
        self.modelReset.connect(self.countChanged)
        for signal in (source.rowsInserted, source.rowsRemoved, source.dataChanged, source.modelReset):
            signal.connect(self._refresh_applications)
        self._refresh_applications()
        self._time_refresh = QTimer(self)
        self._time_refresh.setInterval(60_000)
        self._time_refresh.timeout.connect(self.refreshTimeFilter)

    @Property(str, notify=changed)
    def searchText(self):
        return self._search

    @searchText.setter
    def searchText(self, value):
        value = str(value)
        if value == self._search:
            return
        self._begin_filter_change()
        self._search = value
        self._terms = value.casefold().split()
        self._end_filter_change()
        self.changed.emit()

    @Property(str, notify=changed)
    def modeFilter(self):
        return self._mode

    @modeFilter.setter
    def modeFilter(self, value):
        if value not in {"all", "dictation", "polish", "edit"} or value == self._mode:
            return
        self._begin_filter_change()
        self._mode = value
        self._end_filter_change()
        self.changed.emit()

    @Property(str, notify=changed)
    def outcomeFilter(self):
        return self._outcome

    @outcomeFilter.setter
    def outcomeFilter(self, value):
        if value not in {"all", *self.OUTCOMES} or value == self._outcome:
            return
        self._begin_filter_change()
        self._outcome = value
        self._end_filter_change()
        self.changed.emit()

    def _begin_filter_change(self):
        if hasattr(self, "endFilterChange"):
            self.beginFilterChange()

    def _end_filter_change(self):
        if hasattr(self, "endFilterChange"):
            self.endFilterChange(QSortFilterProxyModel.Direction.Rows)
        else:  # Qt 6.8/6.9 remain supported by the application.
            self.invalidateFilter()

    @Property(str, notify=changed)
    def applicationFilter(self):
        return self._application

    @applicationFilter.setter
    def applicationFilter(self, value):
        if value == self._application or value not in {item['value'] for item in self._applications}:
            return
        self._begin_filter_change()
        self._application = value
        self._end_filter_change()
        self._refresh_applications()
        self.changed.emit()

    @Property(str, notify=changed)
    def timeFilter(self):
        return self._time

    @timeFilter.setter
    def timeFilter(self, value):
        if value not in {"all", "today", "week", "month"} or value == self._time:
            return
        self._begin_filter_change()
        self._time = value
        self._end_filter_change()
        if value == "all":
            self._time_refresh.stop()
        else:
            self._time_refresh.start()
        self.changed.emit()

    @Property("QVariantList", notify=applicationsChanged)
    def applicationOptions(self):
        return self._applications

    def _refresh_applications(self, *_):
        source = self.sourceModel()
        names = {str((source.data(source.index(row, 0), self.ENTRY_ROLE) or {}).get('application') or '')
                 for row in range(source.rowCount())}
        if self._application != 'all':
            names.add('' if self._application == '__unknown__' else self._application)
        options = [{'value': 'all', 'label': '全部应用'}]
        options += [{'value': name or '__unknown__', 'label': application_presentation(name)['name']}
                    for name in sorted(names, key=str.casefold)]
        if options != self._applications:
            self._applications = options
            self.applicationsChanged.emit()

    def _now(self):
        return datetime.now().astimezone()

    @Slot()
    def refreshTimeFilter(self):
        if self._time != 'all':
            self._begin_filter_change()
            self._end_filter_change()

    @Property(int, notify=countChanged)
    def count(self):
        return self.rowCount()

    def filterAcceptsRow(self, row, parent):
        source = self.sourceModel()
        entry = source.data(source.index(row, 0, parent), self.ENTRY_ROLE) or {}
        mode = "edit" if entry.get("mode") == "edit" else "dictation"
        if mode == "dictation" and entry.get("processingMode") == "polish":
            mode = "polish"
        if self._mode != "all" and mode != self._mode:
            return False
        application = str(entry.get('application') or '') or '__unknown__'
        if self._application != 'all' and application != self._application:
            return False
        if self._time != 'all':
            try:
                when = datetime.fromisoformat(str(entry.get('createdAt') or '')).astimezone()
            except (ValueError, TypeError):
                return False
            now = self._now()
            days = {'today': 1, 'week': 7, 'month': 30}[self._time]
            start = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=days - 1)
            if not start <= when <= now:
                return False
        outcome = str(entry.get('outcome') or '')
        if entry.get('recognized') is False and outcome not in {'cancelled', 'cancel', 'undone', 'native_undo_sent', 'interrupted'}:
            outcome = 'unrecognized'
        if self._outcome != "all" and outcome not in self.OUTCOMES[self._outcome]:
            return False
        if not self._terms:
            return True
        fields = [application_presentation(entry.get('application'))['name']]
        fields.extend(str(entry.get(key, "") or "") for key in (
            "text", "candidateText", "editBeforeText", "editSummary", "application"))
        haystack = "\n".join(fields).casefold()
        return all(term in haystack for term in self._terms)
