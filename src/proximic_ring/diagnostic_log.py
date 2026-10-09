"""One chronological log for runtime, scene, console and crash diagnostics."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
import os
from pathlib import Path
import shutil
import tempfile
import threading
import uuid


DIAGNOSTIC_LOG_MAX_BYTES = 8 * 1024 * 1024
DIAGNOSTIC_LOG_BACKUP_COUNT = 5
_states = {}
_states_lock = threading.Lock()
_run_id = uuid.uuid4().hex[:8]
_active_path = None


def diagnostic_run_id():
    return _run_id


def diagnostic_log_path(root=None):
    if _active_path is not None:
        return _active_path
    if root is None:
        from .runtime_paths import app_data_root
        root = app_data_root()
    return Path(root) / 'logs' / 'diagnostic.log'


class _LogState:
    def __init__(self):
        self.lock = threading.RLock()
        self.crash_file = None


def format_record(message, *, source='runtime', level='INFO', run=None):
    stamp = datetime.now().astimezone().isoformat(timespec='milliseconds')
    thread = threading.current_thread().name.replace('\n', ' ')
    prefix = f'[{stamp}] [{level}] [{source}] run={run or _run_id} process={os.getpid()} thread={thread!r} '
    return '\n'.join(prefix + line for line in str(message).rstrip('\r\n').splitlines())


def rotate_existing_log(
    path: Path,
    *,
    max_bytes: int,
    backup_count: int,
    incoming_bytes: int = 0,
) -> bool:
    """Rotate ``path`` when the next write would exceed its byte budget.

    Rotation is deliberately best-effort. Diagnostics must never prevent the
    application from starting or accepting voice input.
    """

    log_path = Path(path)
    try:
        limit = max(1, int(max_bytes))
        backups = max(0, int(backup_count))
        current_size = log_path.stat().st_size if log_path.exists() else 0
        if (
            current_size <= 0
            or current_size + max(0, int(incoming_bytes)) <= limit
        ):
            return False
        if backups <= 0:
            with log_path.open('wb'):
                pass
            return True
        oldest = log_path.with_name(f"{log_path.name}.{backups}")
        oldest.unlink(missing_ok=True)
        for index in range(backups - 1, 0, -1):
            source = log_path.with_name(f"{log_path.name}.{index}")
            if source.exists():
                source.replace(log_path.with_name(f"{log_path.name}.{index + 1}"))
        if os.name == 'nt':
            # Windows cannot rename a log held open by faulthandler. Preserve
            # its append handle while rotating under the shared writer lock.
            shutil.copyfile(log_path, log_path.with_name(f"{log_path.name}.1"))
            with log_path.open('wb'):
                pass
        else:
            log_path.replace(log_path.with_name(f"{log_path.name}.1"))
        return True
    except OSError:
        return False


class RotatingDiagnosticLog:
    """Append UTF-8 lines to a bounded local file without surfacing I/O errors."""

    def __init__(
        self,
        path: Path,
        *,
        max_bytes: int = DIAGNOSTIC_LOG_MAX_BYTES,
        backup_count: int = DIAGNOSTIC_LOG_BACKUP_COUNT,
    ) -> None:
        self.path = Path(path).expanduser().resolve()
        self.max_bytes = max(1, int(max_bytes))
        self.backup_count = max(0, int(backup_count))
        with _states_lock:
            self._state = _states.setdefault(str(self.path), _LogState())
        self._lock = self._state.lock

    @contextmanager
    def locked(self):
        """Serialize rotation and append across threads, writers and app instances."""
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            # Lock a stable sidecar: locking the renamed log inode is unsafe.
            with self.path.with_name('.' + self.path.name + '.lock').open('a+b') as lock:
                if os.name == 'nt':
                    import msvcrt
                    if lock.seek(0, 2) == 0:
                        lock.write(b'\0'); lock.flush()
                    lock.seek(0)
                    msvcrt.locking(lock.fileno(), msvcrt.LK_LOCK, 1)
                else:
                    import fcntl
                    fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    if os.name == 'nt':
                        lock.seek(0)
                        msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    def _refresh_crash_file(self):
        # faulthandler retains the numeric fd. Keep that fd pointed at the
        # active log after rotation, rather than an unlinked/old backup inode.
        handle = self._state.crash_file
        if handle is not None:
            opened, current = os.fstat(handle.fileno()), self.path.stat()
            if (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino):
                with self.path.open('ab', buffering=0) as replacement:
                    os.dup2(replacement.fileno(), handle.fileno())

    def fileno(self):
        with self.locked():
            if self._state.crash_file is None:
                self._state.crash_file = self.path.open('ab', buffering=0)
            self._refresh_crash_file()
            return self._state.crash_file.fileno()

    def record(self, message, *, source='runtime', level='INFO', run=None):
        return self.append(format_record(message, source=source, level=level, run=run))

    def append(self, line: str) -> bool:
        payload = (str(line).rstrip("\r\n") + "\n").encode(
            "utf-8", errors="replace"
        )
        try:
            with self.locked():
                rotate_existing_log(
                    self.path,
                    max_bytes=self.max_bytes,
                    backup_count=self.backup_count,
                    incoming_bytes=len(payload),
                )
                with self.path.open("ab") as handle:
                    handle.write(payload)
                self._refresh_crash_file()
                return True
        except OSError:
            return False

    def files(self):
        return [self.path.with_name(f'{self.path.name}.{i}')
                for i in range(self.backup_count, 0, -1)] + [self.path]

    def tail(self, *, lines=1000, after_marker='', max_bytes=2 * 1024 * 1024):
        """Bounded read of the same file family used by export, newest lines last."""
        chunks, remaining = [], max_bytes
        try:
            with self.locked():
                for path in reversed(self.files()):
                    if not path.is_file() or remaining <= 0:
                        continue
                    with path.open('rb') as handle:
                        size = handle.seek(0, 2)
                        offset = max(0, size - remaining)
                        handle.seek(offset)
                        data = handle.read(remaining)
                    remaining -= len(data)
                    if offset:
                        data = data.partition(b'\n')[2]
                    chunks.append(data)
                    if sum(chunk.count(b'\n') for chunk in chunks) >= lines:
                        break
            text = b''.join(reversed(chunks)).decode('utf-8', errors='replace')
            rows = text.splitlines()[-lines:]
            if after_marker:
                match = next((i for i in range(len(rows) - 1, -1, -1) if after_marker in rows[i]), None)
                if match is not None:
                    rows = rows[match + 1:]
            return '\n'.join(rows)
        except OSError:
            return ''

    def export(self, destination):
        """Create one chronological snapshot of all retained rotations atomically."""
        destination = Path(destination).expanduser().resolve()
        if destination in self.files() or destination == self.path.with_name('.' + self.path.name + '.lock'):
            raise ValueError('导出位置不能覆盖正在使用的日志')
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=destination.parent, prefix='.mythlink-log-', delete=False) as output:
                temporary = Path(output.name)
                with self.locked():
                    for path in self.files():
                        if path.is_file():
                            with path.open('rb') as source:
                                shutil.copyfileobj(source, output)
            temporary.replace(destination)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
