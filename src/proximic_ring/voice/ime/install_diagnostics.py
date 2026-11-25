"""Evidence from a Mythlink IME installation, suitable for a support report."""
from datetime import datetime
import json
import os
from pathlib import Path
import platform
import plistlib
import re
import subprocess

from ...version import app_version


def environment():
    try:
        with Path("/System/Library/CoreServices/SystemVersion.plist").open("rb") as stream:
            version = plistlib.load(stream)
    except (OSError, ValueError):
        version = {}
    if not isinstance(version, dict):
        version = {}
    try:
        application = app_version()
    except (OSError, ValueError, KeyError):
        application = None
    return dict(time=datetime.now().astimezone().isoformat(timespec="seconds"),
                macos=version.get("ProductVersion"), macos_build=version.get("ProductBuildVersion"),
                architecture=platform.machine(), app_version=application)


def diagnostic_detail(error):
    # Paths and helper arguments can include a user name or foreground app.
    # A timeout only needs the duration (subprocess includes argv in its str).
    if isinstance(error, subprocess.TimeoutExpired):
        return f"命令在 {error.timeout} 秒内未返回"
    detail = str(error).replace(os.path.expanduser("~") + "/", "~/")
    detail = re.sub(r"/Users/[^/\s]+/", "~/", detail)
    # Framework warnings can precede the helper's actual error on stderr.
    return detail if len(detail) <= 1200 else detail[:350] + "\n…\n" + detail[-840:]


def source_snapshot(state):
    fields = ("id", "enabled", "enable_capable", "select_capable", "in_enabled_list",
              "selectable", "selected")
    return {**{key: state[key] for key in ("installed", "installed_list_readable", "enabled_list_readable")
               if key in state},
            "sources": [{key: item[key] for key in fields if key in item}
                        for item in state["sources"] if isinstance(item, dict)]}


def activation_issue(state, product):
    """Describe only what TIS reported, without guessing why the OS did it."""
    if state.get("installed_list_readable") is False or state.get("enabled_list_readable") is False:
        return "IME-STATUS", "系统未返回完整的输入源列表"
    sources = state["sources"]
    parent = next((s for s in sources if s.get("id") == product), {})
    mode = next((s for s in sources if s.get("id") == product + ".dictation"), {})
    if not sources:
        return "IME-SOURCE-ABSENT", "系统输入源列表中没有 Mythlink"
    if mode.get("selected") is True and mode.get("selectable") is False:
        return "IME-STATE-CONFLICT", "系统同时报告语音模式已选中、当前不可切换"
    if parent.get("enabled") is False:
        return "IME-PARENT-DISABLED", "系统已列出 Mythlink，但父输入源尚未启用"
    if not mode:
        return "IME-MODE-MISSING", "系统已列出 Mythlink，但没有语音模式"
    if mode.get("enabled") is False:
        return "IME-MODE-DISABLED", "系统已列出语音模式，但尚未启用"
    if mode.get("in_enabled_list") is False:
        return "IME-MODE-NOT-LISTED", "语音模式尚未出现在系统的已启用列表"
    return "IME-MODE-UNAVAILABLE", "系统尚未确认语音模式可切换"


def command_event(stage, args, result):
    name = Path(args[0]).name
    command = args[1] if name == "InputMethodAdmin" else name
    event = dict(stage=stage, command=command, exit_code=result.returncode)
    if result.stderr.strip():
        event["detail"] = diagnostic_detail(result.stderr.strip())
    if command in {"register", "enable", "status"}:
        try:
            data = json.loads(result.stdout)
        except ValueError:
            data = None
        if isinstance(data, dict):
            if "tis_calls" in data:
                event["tis_calls"] = data["tis_calls"]
            if isinstance(data.get("sources"), list):
                event["snapshot"] = source_snapshot(data)
    return event
