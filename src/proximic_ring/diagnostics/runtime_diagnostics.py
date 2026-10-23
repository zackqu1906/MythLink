"""Capture process output without duplicating explicit application log records."""
import atexit
import faulthandler
import io
import logging
from pathlib import Path
import sys
import tempfile
import threading

from . import diagnostic_log
from .diagnostic_log import RotatingDiagnosticLog

_capture = None


class DiagnosticStream(io.TextIOBase):
    def __init__(self, writer, source, *, level='INFO', tee=None):
        self.writer, self.source, self.level, self.tee = writer, source, level, tee
        self._pending = {}
        self._lock = threading.RLock()

    @property
    def encoding(self):
        return 'utf-8'

    def writable(self):
        return True

    def isatty(self):
        return False

    def fileno(self):
        return self.writer.fileno()

    def write(self, value):
        if not value:
            return 0
        with self._lock:
            identity = threading.get_ident()
            pending = self._pending.get(identity, '') + str(value)
            # Bound partial writes (progress output without line endings).
            while '\n' in pending or len(pending) > 65536:
                if '\n' in pending:
                    line, pending = pending.split('\n', 1)
                else:
                    line, pending = pending[:65536], pending[65536:]
                if line.strip():
                    self.writer.record(line.rstrip('\r'), source=self.source, level=self.level)
            if pending:
                self._pending[identity] = pending
            else:
                self._pending.pop(identity, None)
            if self.tee is not None:
                try:
                    self.tee.write(value)
                except Exception:
                    pass
        return len(value)

    def flush(self):
        with self._lock:
            for pending in self._pending.values():
                if pending.strip():
                    self.writer.record(pending, source=self.source, level=self.level)
            self._pending.clear()
            if self.tee is not None:
                try:
                    self.tee.flush()
                except Exception:
                    pass


class DiagnosticHandler(logging.Handler):
    def __init__(self, writer):
        super().__init__()
        self.writer = writer

    def emit(self, record):
        try:
            self.writer.record(self.format(record), source='python.' + record.name, level=record.levelname)
        except Exception:
            pass


class RuntimeCapture:
    def __init__(self, writer, *, tee=False):
        self.writer = writer
        self._closed = False
        self.previous_stdout, self.previous_stderr = sys.stdout, sys.stderr
        self.stdout = DiagnosticStream(writer, 'stdout', tee=sys.stdout if tee else None)
        self.stderr = DiagnosticStream(writer, 'stderr', level='ERROR', tee=sys.stderr if tee else None)
        sys.stdout, sys.stderr = self.stdout, self.stderr
        self.logger = logging.getLogger()
        self.previous_handlers, self.previous_level = self.logger.handlers[:], self.logger.level
        self.logger.handlers = [DiagnosticHandler(writer)]
        self.logger.setLevel(min(self.logger.level, logging.INFO))
        self._qt_previous = None
        self._qt_installed = False
        self._fault_enabled = False
        self._fault_was_enabled = faulthandler.is_enabled()
        try:
            faulthandler.enable(writer.fileno(), all_threads=True)
            self._fault_enabled = True
        except Exception:
            pass

    def install_qt(self):
        if self._qt_installed:
            return
        from PySide6.QtCore import QtMsgType, qInstallMessageHandler
        levels = {QtMsgType.QtDebugMsg: 'DEBUG', QtMsgType.QtInfoMsg: 'INFO',
                  QtMsgType.QtWarningMsg: 'WARNING', QtMsgType.QtCriticalMsg: 'ERROR', QtMsgType.QtFatalMsg: 'CRITICAL'}
        def qt_message(kind, context, message):
            location = f' ({context.file}:{context.line})' if context.file else ''
            self.writer.record(str(message) + location, source='qt.' + str(context.category or 'default'),
                               level=levels.get(kind, 'INFO'))
        self._qt_handler = qt_message
        self._qt_previous = qInstallMessageHandler(qt_message)
        self._qt_installed = True

    def flush(self):
        self.stdout.flush(); self.stderr.flush()

    def close(self):
        global _capture
        if self._closed:
            return
        self._closed = True
        self.flush()
        if sys.stdout is self.stdout:
            sys.stdout = self.previous_stdout
        if sys.stderr is self.stderr:
            sys.stderr = self.previous_stderr
        self.logger.handlers, self.logger.level = self.previous_handlers, self.previous_level
        if self._qt_installed:
            from PySide6.QtCore import qInstallMessageHandler
            qInstallMessageHandler(self._qt_previous)
            self._qt_installed = False
        if self._fault_enabled:
            faulthandler.disable()
            if self._fault_was_enabled and self.previous_stderr is not None:
                try:
                    faulthandler.enable(self.previous_stderr, all_threads=True)
                except Exception:
                    pass
            self._fault_enabled = False
        if _capture is self:
            _capture = None
            diagnostic_log._active_path = None


def configure_diagnostics(*, tee=False):
    global _capture
    if _capture is not None:
        return _capture
    try:
        path = diagnostic_log.diagnostic_log_path()
        writer = RotatingDiagnosticLog(path)
        if not writer.record('diagnostic capture started', source='startup'):
            raise OSError('log path unavailable')
    except Exception:
        path = Path(tempfile.gettempdir()) / 'Mythlink-diagnostic.log'
        writer = RotatingDiagnosticLog(path)
        writer.record('application log directory unavailable; using temporary directory', source='startup', level='WARNING')
    diagnostic_log._active_path = path
    _capture = RuntimeCapture(writer, tee=tee)
    return _capture


def _flush_at_exit():
    if _capture is not None:
        _capture.flush()


atexit.register(_flush_at_exit)
