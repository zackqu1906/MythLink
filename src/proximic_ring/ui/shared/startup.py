"""Opt-in launch at login. Merely opening settings never registers anything."""

import os
from pathlib import Path
import plistlib
import subprocess
import sys
import tempfile

from PySide6.QtCore import QObject, Property, Signal, Slot


class LoginRegistration:
    label = "com.mythlink.launch-at-login"

    def __init__(self, *, home: Path | None = None, platform: str | None = None,
                 executable: str | None = None, frozen: bool | None = None) -> None:
        self.platform = platform or sys.platform
        self.executable = Path(executable or sys.executable).absolute()
        self.frozen = bool(getattr(sys, "frozen", False)) if frozen is None else frozen
        self.path = (home or Path.home()) / "Library/LaunchAgents" / (self.label + ".plist")
        self.source_root = Path(__file__).resolve().parents[3]

    @property
    def supported(self) -> bool:
        return self.platform in {"darwin", "win32"}

    def _arguments(self) -> list[str]:
        if self.frozen:
            if self.platform == "darwin":
                bundle = next((p for p in self.executable.parents if p.suffix == ".app"), None)
                if bundle is None:
                    raise OSError("请从 Mythlink.app 中开启开机启动。")
                return ["/usr/bin/open", "-a", str(bundle)]
            return [str(self.executable)]
        # Preserve the venv executable path; resolving its symlink loses dependencies.
        return [str(self.executable), "-m", "proximic_ring.ui.main"]

    def _plist(self) -> dict:
        value = {"Label": self.label, "ProgramArguments": self._arguments(),
                 "RunAtLoad": True, "LimitLoadToSessionType": "Aqua"}
        if not self.frozen:
            value["WorkingDirectory"] = str(self.source_root.parent)
            value["EnvironmentVariables"] = {"PYTHONPATH": str(self.source_root)}
        return value

    def _windows_command(self) -> str:
        args = self._arguments()
        if not self.frozen:
            # Windows Run entries have no working-directory or environment fields.
            args = [str(self.executable), "-c", "import sys; sys.path.insert(0, "
                    + repr(str(self.source_root))
                    + "); from proximic_ring.ui.main import main; raise SystemExit(main())"]
        return subprocess.list2cmdline(args)

    def enabled(self) -> bool:
        if self.platform == "darwin":
            try:
                value = plistlib.loads(self.path.read_bytes())
                if not isinstance(value, dict):
                    raise ValueError("开机启动配置格式无效。")
                return value.get("Label") == self.label and value.get("RunAtLoad") is True and value.get("ProgramArguments") == self._arguments()
            except FileNotFoundError:
                return False
        if self.platform == "win32":
            import winreg
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run") as key:
                    return winreg.QueryValueEx(key, "Mythlink")[0] == self._windows_command()
            except FileNotFoundError:
                return False
        return False

    def set_enabled(self, enabled: bool) -> None:
        if self.platform == "darwin":
            if not enabled:
                self.path.unlink(missing_ok=True)
                return
            payload = plistlib.dumps(self._plist())
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(dir=self.path.parent, delete=False) as output:
                    temporary = Path(output.name)
                    output.write(payload)
                temporary.chmod(0o644)
                os.replace(temporary, self.path)
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
        elif self.platform == "win32":
            import winreg
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run") as key:
                if enabled:
                    winreg.SetValueEx(key, "Mythlink", 0, winreg.REG_SZ, self._windows_command())
                else:
                    try:
                        winreg.DeleteValue(key, "Mythlink")
                    except FileNotFoundError:
                        pass
        else:
            raise OSError("当前系统暂不支持开机启动。")


class StartupController(QObject):
    changed = Signal()

    def __init__(self, parent: QObject | None = None, *, registration=None) -> None:
        super().__init__(parent)
        self._registration = registration or LoginRegistration()
        self._enabled = False
        self._error = ""
        self.refresh()

    @Property(bool, constant=True)
    def supported(self) -> bool:
        return self._registration.supported

    @Property(bool, notify=changed)
    def enabled(self) -> bool:
        return self._enabled

    @Property(str, notify=changed)
    def error(self) -> str:
        return self._error

    @Slot()
    def refresh(self) -> None:
        try:
            self._enabled = self._registration.enabled()
            self._error = ""
        except (OSError, ValueError, plistlib.InvalidFileException) as exc:
            self._error = "无法读取开机启动设置：" + str(exc)
        self.changed.emit()

    @Slot(bool)
    def setEnabled(self, enabled: bool) -> None:
        try:
            self._registration.set_enabled(enabled)
            self._enabled = self._registration.enabled()
            self._error = "" if self._enabled == enabled else "开机启动设置未生效，请重试。"
        except (OSError, ValueError, plistlib.InvalidFileException) as exc:
            self._error = "无法修改开机启动设置：" + str(exc)
        self.changed.emit()
