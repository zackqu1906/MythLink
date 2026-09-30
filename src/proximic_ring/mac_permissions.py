"""Current-process macOS permission checks; no events, TCC resets or prompts by default."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import hashlib
import subprocess
import sys


@dataclass(frozen=True)
class PermissionState:
    accessibility: bool | None = None
    post_events: bool | None = None
    error: str = ""
    control_channel: str = "current_process"
    control_pid: int = field(default=0, compare=False)

    @property
    def ready(self) -> bool:
        return self.accessibility is True and self.post_events is True

    @property
    def title(self) -> str:
        if self.ready:
            return "辅助功能权限已生效"
        if self.post_events is False and self.accessibility is True:
            return "按键控制权限尚未生效"
        if self.accessibility is False or self.post_events is False:
            return "辅助功能权限尚未生效"
        return "暂时无法确认辅助功能权限"

    @property
    def guidance(self) -> str:
        if self.ready:
            return "按键通道可读取辅助功能信息并发送按键。"
        return ("编辑、撤销和发送可能无法完成。授权后程序会自动重新检测，生效后即可继续操作。"
                "若持续未生效，请核对授权的是当前运行的应用。")


def read_permission_state(*, post_events: bool | None = None) -> PermissionState:
    errors = []
    accessibility = None
    try:
        import ApplicationServices
        accessibility = bool(ApplicationServices.AXIsProcessTrusted())
    except Exception as exc:
        errors.append("accessibility:" + type(exc).__name__)
    if post_events is None:
        try:
            import Quartz
            post_events = bool(Quartz.CGPreflightPostEventAccess())
        except Exception as exc:
            errors.append("post_events:" + type(exc).__name__)
    return PermissionState(accessibility, post_events, ";".join(errors))


class MacPermissionError(RuntimeError):
    def __init__(self, state: PermissionState):
        self.state = state
        super().__init__(state.title + "。授权后会自动重新检测，生效后请重新操作。")


def require_post_event_access() -> None:
    import Quartz
    # Recheck at the operation boundary, never trust a cached UI flag. AX trust
    # alone cannot override a denied event-posting preflight.
    if not Quartz.CGPreflightPostEventAccess():
        raise MacPermissionError(read_permission_state(post_events=False))


def request_post_event_access() -> None:
    """Only called by an explicit permission button, never by voice/gesture actions."""
    import Quartz
    Quartz.CGRequestPostEventAccess()


def request_accessibility_access() -> bool:
    """Ask macOS to present its own alert; never open Settings over that alert."""
    import ApplicationServices as AX
    if not AX.AXIsProcessTrusted():
        return bool(AX.AXIsProcessTrustedWithOptions({AX.kAXTrustedCheckOptionPrompt: True}))
    import Quartz
    return bool(Quartz.CGPreflightPostEventAccess() or Quartz.CGRequestPostEventAccess())


def read_screen_capture_access() -> bool:
    """Check the GUI process that actually owns the preview streams."""
    import Quartz
    return bool(Quartz.CGPreflightScreenCaptureAccess())


def request_screen_capture_access() -> bool:
    """Called by onboarding or a permission button, never a passive check."""
    import Quartz
    return read_screen_capture_access() or bool(Quartz.CGRequestScreenCaptureAccess())


def running_identity() -> dict:
    executable = Path(sys.executable).resolve()
    frozen = bool(getattr(sys, "frozen", False))
    bundle = next((p for p in executable.parents if p.suffix == ".app"), None) if frozen else None
    path = str(bundle or executable)
    return {"frozen": frozen, "executable": str(executable), "app_path": str(bundle or ""),
            "temporary_location": bool(bundle and (path.startswith("/Volumes/") or "/AppTranslocation/" in path))}


def permission_request_scope() -> str:
    """Keep source, installed copies and differently signed builds independent.

    A Developer ID designated requirement is stable across normal updates;
    an ad-hoc requirement contains the changing code hash, as macOS TCC does.
    This reads signing metadata only; it never resets or edits TCC.
    """
    identity = running_identity()
    executable = Path(identity["executable"])
    signature = "source"
    if identity["frozen"]:
        try:
            result = subprocess.run(["/usr/bin/codesign", "-d", "-r-", str(executable)],
                                    capture_output=True, text=True, timeout=2, check=True)
            signature = next(line for line in (result.stdout + result.stderr).splitlines()
                             if line.startswith("designated =>"))
        except (OSError, subprocess.SubprocessError, StopIteration):
            stat = executable.stat()
            signature = f"unsigned:{stat.st_mtime_ns}:{stat.st_size}"
    else:
        signature += ":" + str(Path(__file__).resolve().parents[2])
    raw = f"{identity['frozen']}|{identity['app_path'] or executable}|{signature}"
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


def permission_status_report(app, settings) -> dict:
    """Read-only package diagnostic; no requests, windows or settings writes."""
    from dataclasses import asdict
    from PySide6.QtCore import QBluetoothPermission, QMicrophonePermission, qInstallMessageHandler
    warnings = []
    previous = None
    def message(kind, context, text):
        if "permission plugin" in text.lower() and "could not" in text.lower():
            warnings.append(text)
        if previous:
            previous(kind, context, text)
    previous = qInstallMessageHandler(message)
    try:
        devices = {kind: app.checkPermission(permission()).name for kind, permission in
                   (("bluetooth", QBluetoothPermission), ("microphone", QMicrophonePermission))}
    finally:
        qInstallMessageHandler(previous)
    scope = permission_request_scope()
    return dict(**devices, **asdict(read_permission_state()), screen=read_screen_capture_access(),
                request_scope=scope, native_requests=settings.value("onboarding/nativePermissionsV3/" + scope, []),
                legacy_shared_record=settings.value("onboarding/nativePermissionsV2Attempted", []),
                backend_errors=warnings)
