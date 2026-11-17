"""Open or activate a selected macOS application without blocking Qt."""
from PySide6.QtCore import QObject, QProcess, QTimer


class ApplicationLauncher(QObject):
    def __init__(self, parent):
        super().__init__(parent)
        self._pending = {}

    def open(self, application, completed):
        process = QProcess(self)
        timer = QTimer(process)
        timer.setSingleShot(True)
        self._pending[process] = timer

        def finish(success, detail=""):
            if process not in self._pending:
                return
            self._pending.pop(process).stop()
            if process.state() != QProcess.NotRunning:
                process.kill()
            process.deleteLater()
            completed(success, detail)

        def exited(code, status):
            success = code == 0 and status == QProcess.NormalExit
            finish(success, "" if success else "open_exit_" + str(code))

        process.finished.connect(exited)
        process.errorOccurred.connect(lambda error: finish(False, "open_unavailable")
                                     if error == QProcess.FailedToStart else None)
        timer.timeout.connect(lambda: finish(False, "open_timeout"))
        # Bundle identity survives moving/renaming the .app. No shell, documents,
        # extra arguments, or new-instance flag: running apps are brought forward.
        timer.start(5000)
        process.start("/usr/bin/open", ["-b", application.bundle])

    def close(self):
        for process, timer in list(self._pending.items()):
            timer.stop()
            process.kill()
        self._pending.clear()
