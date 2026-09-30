"""Presentation-only search and filters over the live history projection."""
from PySide6.QtCore import Property, QSortFilterProxyModel, Signal, Qt


class VoiceHistoryFilter(QSortFilterProxyModel):
    changed = Signal()
    countChanged = Signal()
    ENTRY_ROLE = int(Qt.ItemDataRole.UserRole) + 1
    OUTCOMES = {
        "completed": {"applied", "confirm"},
        "undone": {"undone", "native_undo_sent"},
        "failed": {"apply_failed", "abandoned"},
        "cancelled": {"cancelled", "cancel"},
    }

    def __init__(self, source, parent=None):
        super().__init__(parent)
        self._search = ""
        self._mode = "all"
        self._outcome = "all"
        self._terms = []
        self.setFilterRole(self.ENTRY_ROLE)
        self.setSourceModel(source)
        self.rowsInserted.connect(self.countChanged)
        self.rowsRemoved.connect(self.countChanged)
        self.modelReset.connect(self.countChanged)

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
        if value not in {"all", "dictation", "edit"} or value == self._mode:
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

    @Property(int, notify=countChanged)
    def count(self):
        return self.rowCount()

    def filterAcceptsRow(self, row, parent):
        source = self.sourceModel()
        entry = source.data(source.index(row, 0, parent), self.ENTRY_ROLE) or {}
        mode = "edit" if entry.get("mode") == "edit" else "dictation"
        if self._mode != "all" and mode != self._mode:
            return False
        if self._outcome != "all" and entry.get("outcome") not in self.OUTCOMES[self._outcome]:
            return False
        haystack = "\n".join(str(entry.get(key, "") or "") for key in (
            "text", "candidateText", "editBeforeText", "editSummary", "application",
        )).casefold()
        return all(term in haystack for term in self._terms)
