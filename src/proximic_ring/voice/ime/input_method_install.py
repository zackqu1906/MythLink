"""Install the bundled macOS IME, temporarily releasing it when selected."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
import logging
import os
from pathlib import Path
import plistlib
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from xml.parsers.expat import ExpatError

from .component_fingerprint import SOURCE_FINGERPRINT_KEY, source_fingerprint
from .install_diagnostics import activation_issue, command_event, diagnostic_detail, environment, source_snapshot

BUNDLE_ID = "com.proximic.inputmethod.ProxiMicVoice"
ACTIVATION_GUIDANCE = ("在“系统设置 → 键盘 → 文字输入 → 编辑”中点“＋”，在“简体中文”下添加“Mythlink 语音”。"
                       "如果列表中没有它，请保存工作，注销并重新登录 Mac 后再添加。")
# The helper uses exit 2 only when the registered mode is not yet available.
ENABLE_PENDING = 2
_ENABLE_DELAYS = (0, .25, .75, 1.0)
_logger = logging.getLogger(__name__)


class InstallFailure(RuntimeError):
    def __init__(self, code, detail):
        self.code = code
        super().__init__(f"[{code}] {detail}")


def _dictation_available(state: dict) -> bool:
    return any(source.get("id") == BUNDLE_ID + ".dictation"
               and source.get("enabled") is True and source.get("selectable") is True
               for source in state["sources"])


def _version_parts(value: str) -> tuple[int, ...]:
    parts = tuple(int(part) for part in value.split("."))
    while len(parts) > 1 and parts[-1] == 0:
        parts = parts[:-1]
    return parts


def _bundle_version(path: Path) -> tuple[str, str] | None:
    """Read both version numbers without launching or selecting the IME."""
    if path.is_symlink() or not (path / "Contents/MacOS/ProxiMicInput").is_file():
        return None
    try:
        with (path / "Contents/Info.plist").open("rb") as stream:
            info = plistlib.load(stream)
        if not isinstance(info, dict) or info.get("CFBundleIdentifier") != BUNDLE_ID:
            return None
        values = (info.get("CFBundleShortVersionString"), info.get("CFBundleVersion"))
        if not all(isinstance(value, str) and re.fullmatch(r"[0-9]+(?:\.[0-9]+)*", value) for value in values):
            return None
        return values
    except (OSError, ValueError, ExpatError):
        return None


def payload_directory() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent.parent / "Helpers"
    return Path(__file__).resolve().parents[4] / ".build/input-method"


def product_bundle(path: Path) -> bool:
    if path.is_symlink():
        return False
    try:
        with (path / "Contents/Info.plist").open("rb") as stream:
            info = plistlib.load(stream)
        return isinstance(info, dict) and info.get("CFBundleIdentifier") == BUNDLE_ID
    except (OSError, ValueError, ExpatError):
        return False


def _fingerprint(path: Path, key: str) -> str:
    try:
        with (path / "Contents/Info.plist").open("rb") as stream:
            value = plistlib.load(stream).get(key, "")
        return value if isinstance(value, str) else ""
    except (OSError, ValueError, AttributeError, ExpatError):
        return ""


class InputMethodInstaller:
    def __init__(self, payload: Path | None = None):
        self.payload = payload if payload is not None else payload_directory()
        self.helper = self.payload / "InputMethodAdmin"
        self.source = self.payload / "ProxiMicInput.app"
        self.destination = Path.home() / "Library/Input Methods/ProxiMicInput.app"
        self.system_destination = Path("/Library/Input Methods/ProxiMicInput.app")
        self.diagnostic = {}
        self._stage = ""
        self._started = 0.0

    def _development_root(self) -> Path | None:
        root = self.payload.parent.parent
        if (not getattr(sys, "frozen", False) and self.payload.name == "input-method"
                and self.payload.parent.name == ".build"
                and (root / "native/ProxiMicInput/Info.plist").is_file()):
            return root
        return None

    def _needs_build(self) -> bool:
        root = self._development_root()
        return root is not None and source_fingerprint(root) != _fingerprint(self.source, SOURCE_FINGERPRINT_KEY)

    def _build_if_needed(self) -> None:
        if not self._needs_build():
            return
        root = self._development_root()
        result = subprocess.run([sys.executable, str(root / "tools/build_input_method.py")],
                                cwd=root, capture_output=True, text=True, timeout=300)
        if result.returncode:
            detail = (result.stderr.strip() or result.stdout.strip())[-2000:]
            raise RuntimeError("输入法构建失败，已安装组件保持不变。" + detail)
        if self._needs_build():
            raise RuntimeError("输入法源码仍有未构建的更改，已安装组件保持不变。")

    def version_status(self) -> dict:
        """Compare installed release/build numbers with this application's payload."""
        installed_path = self.destination
        if not installed_path.exists() and not installed_path.is_symlink():
            installed_path = self.system_destination
        installed = _bundle_version(installed_path)
        bundled = _bundle_version(self.source)
        label = lambda value: f"{value[0]} ({value[1]})" if value else "无法读取"
        present = installed_path.exists() or installed_path.is_symlink()
        installed_detail = f"已安装：{label(installed)}" if present else "尚未安装"
        detail = f"{installed_detail}；随应用提供：{label(bundled)}"
        try:
            if self._needs_build():
                return dict(state="needs_build", text="待构建更新", current=False,
                            detail=detail + "；输入法源码有更新，点击更新将先构建再安装。")
        except OSError:
            return dict(state="unknown", text="版本待确认", current=False,
                        detail=detail + "；无法校验输入法源码，请检查源码是否完整。")
        if not present:
            state, text = "not_installed", "未安装"
        elif installed is None or bundled is None:
            state, text = "unknown", "版本待确认"
        else:
            installed_key = tuple(_version_parts(value) for value in installed)
            bundled_key = tuple(_version_parts(value) for value in bundled)
            if installed_key < bundled_key:
                state, text = "outdated", "待更新"
            elif installed_key > bundled_key:
                state, text = "newer", "已是更高版本"
            else:
                installed_fingerprint = _fingerprint(installed_path, "MythlinkInputMethodFingerprint")
                bundled_fingerprint = _fingerprint(self.source, "MythlinkInputMethodFingerprint")
                if bundled_fingerprint and installed_fingerprint != bundled_fingerprint:
                    state, text = "outdated", "待更新"
                else:
                    state, text = "current", "已是最新版"
        return dict(state=state, text=text, current=state in {"current", "newer"}, detail=detail)

    @property
    def available(self) -> bool:
        return (sys.platform == "darwin" and self.helper.is_file()
                and product_bundle(self.source)
                and (self.source / "Contents/MacOS/ProxiMicInput").is_file())

    def _run(self, args: list[str], *, check: bool = True) -> subprocess.CompletedProcess:
        try:
            result = subprocess.run(args, capture_output=True, text=True, timeout=15)
        except (OSError, subprocess.SubprocessError) as exc:
            if self._stage:
                self.diagnostic["events"].append(dict(stage=self._stage,
                    elapsed_ms=round((time.monotonic() - self._started) * 1000), command=Path(args[0]).name,
                    operation=args[1] if args[0] == str(self.helper) else None,
                    exception=type(exc).__name__, errno=getattr(exc, "errno", None), detail=diagnostic_detail(exc)))
            raise
        if self._stage and (args[0] == str(self.helper) or args[0] == "/usr/bin/codesign" or result.returncode > 1):
            event = command_event(self._stage, args, result)
            event["elapsed_ms"] = round((time.monotonic() - self._started) * 1000)
            self.diagnostic["events"].append(event)
        if check and result.returncode:
            raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "输入法安装命令未完成")
        return result

    def verify_payload(self) -> None:
        if self._needs_build():
            raise RuntimeError("输入法源码有未构建的更改，请先构建或点击更新输入法。")
        if not self.available:
            if not self.helper.is_file():
                detail = "安装包缺少 InputMethodAdmin 安装工具"
            elif not self.source.is_dir():
                detail = "安装包缺少 ProxiMicInput.app 组件"
            elif not product_bundle(self.source):
                detail = "输入法组件的标识或 Info.plist 无效"
            elif not (self.source / "Contents/MacOS/ProxiMicInput").is_file():
                detail = "输入法组件缺少 ProxiMicInput 可执行文件"
            else:
                detail = "此安装工具仅支持 macOS"
            raise RuntimeError(detail + "。请重新安装完整应用。源码运行请先构建输入法组件。")
        for path, code in ((self.source, "IME-SIGN-BUNDLE"), (self.helper, "IME-SIGN-HELPER")):
            try:
                self._run(["/usr/bin/codesign", "--verify", "--strict", str(path)])
            except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
                raise InstallFailure(code, diagnostic_detail(exc)) from exc
        for path in (self.helper, self.source / "Contents/MacOS/ProxiMicInput"):
            if not os.access(path, os.X_OK):
                raise InstallFailure("IME-EXEC-PERMISSION", f"{path.name} 缺少执行权限，请重新安装完整应用。")

    def status(self) -> dict:
        if sys.platform != "darwin" or not self.helper.is_file():
            raise InstallFailure("IME-STATUS", "未找到 macOS 输入法安装工具")
        try:
            state = json.loads(self._run([str(self.helper), "status"]).stdout)
        except (ValueError, OSError, RuntimeError, subprocess.SubprocessError) as exc:
            raise InstallFailure("IME-STATUS", f"无法读取当前输入法状态：{diagnostic_detail(exc)}") from exc
        if (not isinstance(state, dict) or not isinstance(state.get("sources"), list)
                or not all(isinstance(source, dict) for source in state["sources"])):
            raise InstallFailure("IME-STATUS", "输入法工具返回的状态格式无效")
        return state

    def activation_status(self, version: dict) -> dict:
        """Refine an up-to-date disk version with a read-only, fresh TIS snapshot."""
        try:
            state = self.status()
            if _dictation_available(state):
                return version
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
            return dict(state="activation_unknown", text="启用状态待确认", current=False, code="IME-STATUS",
                        detail=f"{version['detail']}。{exc}")
        code, reason = activation_issue(state, BUNDLE_ID)
        return dict(state="activation_unknown" if code == "IME-STATUS" else "activation_pending",
                    text="启用状态待确认" if code == "IME-STATUS" else "已安装，待启用", current=False,
                    code=code, snapshot=source_snapshot(state),
                    detail=f"{version['detail']}。[{code}] {reason}。{ACTIVATION_GUIDANCE}")

    def _enable_registered_mode(self) -> dict:
        for delay in _ENABLE_DELAYS:
            if delay:
                time.sleep(delay)
            result = self._run([str(self.helper), "enable"], check=False)
            if result.returncode not in (0, ENABLE_PENDING):
                raise RuntimeError(result.stderr.strip() or "系统启用输入法失败")
            state = self.status()
            if state.get("installed") is not True:
                raise InstallFailure("IME-INSTALLED-BUNDLE", "系统无法读取刚复制的输入法组件。")
            _logger.info("[INPUT_METHOD_INSTALL] enable=%s detail=%s sources=%s",
                         result.returncode, result.stderr.strip(), state["sources"])
            if _dictation_available(state):
                return state
        code, reason = activation_issue(state, BUNDLE_ID)
        return {**state, "activation_pending": True, "code": code, "reason": reason}

    def _require_unselected(self) -> None:
        if any(source.get("selected") for source in self.status()["sources"]):
            raise RuntimeError("请先手动切回拼音或 ABC，再点击安装／更新输入法。")

    def _prepare_update(self) -> dict | None:
        if not any(source.get("selected") for source in self.status()["sources"]):
            return None
        result = json.loads(self._run([str(self.helper), "prepare-update"]).stdout)
        if not isinstance(result, dict) or "update_handoff" not in result:
            raise RuntimeError("输入法更新工具未返回交接信息，原组件未被替换。")
        handoff = result["update_handoff"]
        if handoff is not None and (not isinstance(handoff, dict)
                or handoff.get("originalSource") not in {BUNDLE_ID, BUNDLE_ID + ".dictation"}
                or not isinstance(handoff.get("temporarySource"), str)
                or not handoff["temporarySource"].startswith("com.apple.keylayout.")
                or type(handoff.get("pid")) is not int or handoff["pid"] <= 0
                or not isinstance(handoff.get("bundle"), str) or not handoff["bundle"]):
            raise RuntimeError("输入法更新交接信息无效，原组件未被替换。")
        return handoff

    def _restore_after_update(self, handoff: dict | None) -> dict:
        if handoff is None:
            return {"input_source_switched": False}
        try:
            result = json.loads(self._run([
                str(self.helper), "restore-after-update", json.dumps(handoff)]).stdout)
            restored = result.get("input_source_restored")
            if type(restored) is not bool:
                raise RuntimeError("系统未确认输入法恢复结果")
            return {"input_source_switched": True, "input_source_restored": restored}
        except Exception as exc:
            return {"input_source_switched": True, "input_source_restored": False,
                    "input_source_restore_error": str(exc)}

    def _stop_inactive_component(self) -> None:
        result = self._run(["/usr/bin/pgrep", "-x", "ProxiMicInput"], check=False)
        if result.returncode == 1:
            return
        if result.returncode != 0:
            raise RuntimeError("无法检查旧输入法进程，请退出旧组件后重试。")
        expected = str(self.destination / "Contents/MacOS/ProxiMicInput")
        pids = []
        for value in result.stdout.split():
            pid = int(value)
            info = self._run(["/bin/ps", "-p", str(pid), "-o", "uid=,command="], check=False).stdout.strip()
            if not info:
                continue
            uid, command = info.split(None, 1)
            if int(uid) != os.getuid():
                continue
            if command != expected:
                raise RuntimeError("另一份 Mythlink 输入法仍在运行，请退出该组件后重试。")
            pids.append(pid)
        self._require_unselected()
        for pid in pids:
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        deadline = time.monotonic() + 5
        while pids:
            pids = [pid for pid in pids if self._run(
                ["/bin/ps", "-p", str(pid), "-o", "pid="], check=False).stdout.strip()]
            if not pids:
                break
            if time.monotonic() >= deadline:
                raise RuntimeError("旧输入法尚未退出，请稍后重试；现有组件没有被覆盖。")
            time.sleep(.1)

    def install(self) -> dict:
        def bundle_info(path):
            try:
                return dict(present=path.exists() or path.is_symlink(), version=_bundle_version(path))
            except OSError as exc:
                return dict(read_error=diagnostic_detail(exc), errno=exc.errno)

        self.diagnostic = {**environment(), "bundled": bundle_info(self.source),
                           "user_before": bundle_info(self.destination),
                           "system_before": bundle_info(self.system_destination), "events": []}
        self._started = time.monotonic()
        self._stage = "IME-BUILD"
        try:
            result = self._install()
            if result.get("activation_pending"):
                self.diagnostic.update(outcome="pending", code=result["code"], detail=result["reason"])
            elif result.get("input_source_restore_error"):
                self.diagnostic.update(outcome="installed_with_warning", code="IME-RESTORE",
                                       detail=diagnostic_detail(result["input_source_restore_error"]))
            else:
                self.diagnostic.update(outcome="ready", code="OK")
            return result
        except Exception as exc:
            code = getattr(exc, "code", self._stage)
            self.diagnostic.update(outcome="failed", code=code, detail=diagnostic_detail(exc),
                                   exception=type(exc).__name__, errno=getattr(exc, "errno", None))
            if isinstance(exc, InstallFailure):
                raise
            raise InstallFailure(code, diagnostic_detail(exc)) from exc
        finally:
            self._stage = ""
            _logger.info("[INPUT_METHOD_INSTALL_REPORT] %s", json.dumps(self.diagnostic, ensure_ascii=False))

    @contextmanager
    def _staging_directory(self):
        directory = Path(tempfile.mkdtemp(prefix=".ProxiMic-install-", dir=self.destination.parent))
        try:
            yield directory
        finally:
            rollback = self.diagnostic.get("rollback", {})
            if rollback.get("error") and (directory / "previous.app").exists():
                # Never discard the user's only previous copy if restoring the file failed.
                rollback["saved_backup"] = f"{directory.name}/previous.app"
            else:
                shutil.rmtree(directory)

    def _rollback(self, installed: bool, backup: Path, original_error: BaseException) -> None:
        failed_stage = self._stage
        rollback = self.diagnostic["rollback"] = {"bundle": "unchanged"}
        try:
            if installed:
                self._stage = "before_rollback"
                try:
                    rollback["before"] = source_snapshot(self.status())
                except Exception as probe_error:
                    rollback["probe_error"] = diagnostic_detail(probe_error)
            self._stage = "IME-ROLLBACK"
            if installed and self.destination.exists():
                shutil.rmtree(self.destination)
                rollback["bundle"] = "new_removed"
            if backup.exists():
                os.replace(backup, self.destination)
                rollback["bundle"] = "previous_restored"
                for command in ("register", "enable"):
                    result = self._run([str(self.helper), command], check=False)
                    rollback[command + "_exit"] = result.returncode
        except Exception as recovery_error:
            rollback["error"] = diagnostic_detail(recovery_error)
            raise InstallFailure(getattr(original_error, "code", failed_stage),
                                 f"{original_error}；[IME-ROLLBACK] {rollback['error']}") from recovery_error
        finally:
            self._stage = failed_stage

    def _install(self) -> dict:
        self._build_if_needed()
        self._stage = "IME-PAYLOAD"
        self.verify_payload()
        self.diagnostic["bundled"] = dict(present=True, version=_bundle_version(self.source))
        self._stage = "IME-DESTINATION"
        if self.destination.exists() or self.destination.is_symlink():
            if not product_bundle(self.destination):
                raise RuntimeError("目标位置不是本产品的输入法组件，不会覆盖。")
        self._stage = "IME-COPY"
        self.destination.parent.mkdir(parents=True, exist_ok=True)
        with self._staging_directory() as directory:
            staged = directory / self.destination.name
            backup = directory / "previous.app"
            shutil.copytree(self.source, staged, symlinks=True)
            self._stage = "IME-SIGN-COPY"
            self._run(["/usr/bin/codesign", "--verify", "--strict", str(staged)])
            # Stage and verify before touching the user's input source or process.
            self._stage = "IME-HANDOFF"
            handoff = self._prepare_update()
            try:
                self._require_unselected()
                self._stage = "IME-PROCESS"
                self._stop_inactive_component()
                self._stage = "IME-HANDOFF"
                self._require_unselected()
                installed = False
                try:
                    self._stage = "IME-REPLACE"
                    if self.destination.exists():
                        os.replace(self.destination, backup)
                    os.replace(staged, self.destination)
                    installed = True
                    self._stage = "IME-REGISTER"
                    self._run([str(self.helper), "register"])
                    _logger.info("[INPUT_METHOD_INSTALL] registered sources=%s", self.status()["sources"])
                    self._stage = "IME-ENABLE"
                    state = self._enable_registered_mode()
                    if state.get("activation_pending") and (backup.exists() or product_bundle(self.system_destination)):
                        raise InstallFailure(state["code"], "系统尚未确认新版 Mythlink 语音模式可切换。"
                                             + state["reason"] + "。")
                except BaseException as exc:
                    self._rollback(installed, backup, exc)
                    raise
            except BaseException as exc:
                failed_stage = self._stage
                self._stage = "IME-RESTORE"
                restored = self._restore_after_update(handoff)
                self._stage = failed_stage
                if restored.get("input_source_restore_error"):
                    self.diagnostic["restore_error"] = diagnostic_detail(restored["input_source_restore_error"])
                    raise InstallFailure(getattr(exc, "code", failed_stage),
                                         f"{exc}；未能恢复输入法选择 [IME-RESTORE]，请从菜单栏选择 Mythlink。") from exc
                raise
            # A restoration failure must not roll back a successfully installed bundle.
            self._stage = "IME-RESTORE"
            return {**state, **self._restore_after_update(handoff)}

    def uninstall(self) -> None:
        self._require_unselected()
        if self.destination.exists() or self.destination.is_symlink():
            if not product_bundle(self.destination):
                raise RuntimeError("目标位置不是本产品的输入法组件，不会删除。")
        self._stop_inactive_component()
        self._require_unselected()
        self._run([str(self.helper), "disable"])
        if self.destination.exists():
            shutil.rmtree(self.destination)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("install", "uninstall", "status", "verify"), nargs="?", default="install")
    args = parser.parse_args(argv)
    installer = InputMethodInstaller()
    try:
        if args.action == "status":
            print(json.dumps(installer.status(), ensure_ascii=False, indent=2))
        elif args.action == "verify":
            installer.verify_payload()
            print("输入法安装资源校验通过")
        elif args.action == "uninstall":
            installer.uninstall()
            print("已移除当前用户的 Mythlink 输入法。主程序、模型和用户数据保留。")
        else:
            result = installer.install()
            if result.get("activation_pending"):
                print(f"组件已安装，等待系统启用。[{result['code']}] {result['reason']}。{ACTIVATION_GUIDANCE}")
            elif result.get("input_source_restore_error"):
                print("已安装并启用；未能自动恢复输入法，请从菜单栏选择 Mythlink。")
            elif result.get("input_source_restored"):
                print("已安装并启用，已恢复 Mythlink 输入法。")
            else:
                print("已安装并启用语音输入法。请点入文本框后触发开始语音手势，或从菜单栏选择 Mythlink。")
        return 0
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
