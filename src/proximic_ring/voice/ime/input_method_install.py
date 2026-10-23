"""Install the bundled macOS IME, temporarily releasing it when selected."""
from __future__ import annotations

import argparse
import json
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

BUNDLE_ID = "com.proximic.inputmethod.ProxiMicVoice"


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
            return plistlib.load(stream).get("CFBundleIdentifier") == BUNDLE_ID
    except (OSError, ValueError):
        return False


class InputMethodInstaller:
    def __init__(self, payload: Path | None = None):
        self.payload = payload if payload is not None else payload_directory()
        self.helper = self.payload / "InputMethodAdmin"
        self.source = self.payload / "ProxiMicInput.app"
        self.destination = Path.home() / "Library/Input Methods/ProxiMicInput.app"
        self.system_destination = Path("/Library/Input Methods/ProxiMicInput.app")

    def version_status(self) -> dict:
        """Compare installed release/build numbers with this application's payload."""
        installed_path = self.destination
        if not installed_path.exists() and not installed_path.is_symlink():
            installed_path = self.system_destination
        installed = _bundle_version(installed_path)
        bundled = _bundle_version(self.source)
        label = lambda value: f"{value[0]} ({value[1]})" if value else "无法读取"
        detail = f"已安装：{label(installed)}；随应用提供：{label(bundled)}"
        if not installed_path.exists() and not installed_path.is_symlink():
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
                state, text = "current", "已是最新版"
        return dict(state=state, text=text, current=state in {"current", "newer"}, detail=detail)

    @property
    def available(self) -> bool:
        return (sys.platform == "darwin" and self.helper.is_file()
                and product_bundle(self.source)
                and (self.source / "Contents/MacOS/ProxiMicInput").is_file())

    def _run(self, args: list[str], *, check: bool = True) -> subprocess.CompletedProcess:
        result = subprocess.run(args, capture_output=True, text=True, timeout=15)
        if check and result.returncode:
            raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "输入法安装命令未完成")
        return result

    def verify_payload(self) -> None:
        if not self.available:
            raise RuntimeError("安装包缺少语音输入法组件，请重新安装完整应用。源码运行请先构建输入法组件。")
        self._run(["/usr/bin/codesign", "--verify", "--strict", str(self.source)])
        self._run(["/usr/bin/codesign", "--verify", "--strict", str(self.helper)])
        for path in (self.helper, self.source / "Contents/MacOS/ProxiMicInput"):
            if not os.access(path, os.X_OK):
                raise RuntimeError("输入法组件缺少执行权限，请重新安装完整应用。")

    def status(self) -> dict:
        if sys.platform != "darwin" or not self.helper.is_file():
            raise RuntimeError("未找到 macOS 输入法安装工具")
        state = json.loads(self._run([str(self.helper), "status"]).stdout)
        if not isinstance(state.get("sources"), list):
            raise RuntimeError("无法读取当前输入法状态")
        return state

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
        self.verify_payload()
        if self.destination.exists() or self.destination.is_symlink():
            if not product_bundle(self.destination):
                raise RuntimeError("目标位置不是本产品的输入法组件，不会覆盖。")
        self.destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=".ProxiMic-install-", dir=self.destination.parent) as directory:
            staged = Path(directory) / self.destination.name
            backup = Path(directory) / "previous.app"
            shutil.copytree(self.source, staged, symlinks=True)
            self._run(["/usr/bin/codesign", "--verify", "--strict", str(staged)])
            # Stage and verify before touching the user's input source or process.
            handoff = self._prepare_update()
            try:
                self._require_unselected()
                self._stop_inactive_component()
                self._require_unselected()
                installed = False
                try:
                    if self.destination.exists():
                        os.replace(self.destination, backup)
                    os.replace(staged, self.destination)
                    installed = True
                    self._run([str(self.helper), "register"])
                    self._run([str(self.helper), "enable"])
                    state = self.status()
                    if not state.get("installed") or not any(s.get("enabled") for s in state["sources"]):
                        raise RuntimeError("组件已复制，但系统尚未确认输入法启用。")
                except BaseException:
                    if installed and self.destination.exists():
                        shutil.rmtree(self.destination)
                    if backup.exists():
                        os.replace(backup, self.destination)
                        self._run([str(self.helper), "register"], check=False)
                        self._run([str(self.helper), "enable"], check=False)
                    raise
            except BaseException as exc:
                restored = self._restore_after_update(handoff)
                if restored.get("input_source_restore_error"):
                    raise RuntimeError(f"{exc}；未能恢复输入法选择，请从菜单栏选择 Mythlink。") from exc
                raise
            # A restoration failure must not roll back a successfully installed bundle.
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
            if result.get("input_source_restore_error"):
                print("已安装并启用；未能自动恢复输入法，请从菜单栏选择 Mythlink。")
            elif result.get("input_source_restored"):
                print("已安装并启用，已恢复 Mythlink 输入法。")
            else:
                print("已安装并启用语音输入法。请点入文本框后双击 tap，或从菜单栏选择 Mythlink。")
        return 0
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
