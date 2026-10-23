"""Local display-only profile for the account preview; no authentication."""

from PySide6.QtCore import QObject, Property, QSettings, Signal, Slot


class LocalProfile(QObject):
    changed = Signal()

    def __init__(self, settings: QSettings, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._settings = settings
        self._name = str(settings.value("ui/localProfile/name", ""))
        self._email = str(settings.value("ui/localProfile/email", ""))
        self._signed_in = settings.value("ui/localProfile/signedIn", False, type=bool)

    @Property(str, notify=changed)
    def displayName(self) -> str:
        return self._name

    @Property(str, notify=changed)
    def email(self) -> str:
        return self._email

    @Property(bool, notify=changed)
    def signedIn(self) -> bool:
        return self._signed_in

    @Slot(str, str)
    def save(self, name: str, email: str) -> None:
        self._name, self._email, self._signed_in = name, email, True
        self._settings.setValue("ui/localProfile/name", name)
        self._settings.setValue("ui/localProfile/email", email)
        self._settings.setValue("ui/localProfile/signedIn", True)
        self._settings.sync()
        self.changed.emit()

    @Slot()
    def signOut(self) -> None:
        self._name, self._email, self._signed_in = "", "", False
        self._settings.remove("ui/localProfile")
        self._settings.sync()
        self.changed.emit()
