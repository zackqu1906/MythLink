"""Bounded scene-operation diagnostics, independent of Qt and native access.

Only control metadata and decision codes belong here. Never pass UI text, file
names, URLs, AX objects, transcript content or raw exception messages.
"""
from collections import deque
from datetime import datetime
import json
import platform
import os
import sys
import hashlib
from pathlib import Path
import traceback
import threading
import uuid

from .diagnostic_log import RotatingDiagnosticLog

SCHEMA = 1
MESSAGES = {
    'session_started': '场景日志已启动',
    'ring_route_blocked': '当前手势模式或选择操作暂不允许此动作',
    'old_connection': '戒指连接已更换，本次操作已取消',
    'old_gesture_settings': '手势设置已变化，本次操作已取消',
    'disconnecting': '戒指正在断开连接', 'runtime_inactive': '手势服务未运行',
    'disconnected': '戒指已断开连接',
    'unsupported_media_kind': '当前媒体类型不在此应用支持的场景中',
    'received': '已收到手势，正在检查场景', 'dispatch_started': '正在检查目标并发送快捷键',
    'captured': '已读取当前应用与焦点', 'target_verified': '目标检查通过',
    'unsupported_platform': '当前系统不支持此原生场景通道',
    'unsupported_kind': '未发现此应用可用的阅读或播放场景',
    'pending_commit': '正在等待本句定稿，暂不执行其他应用动作',
    'recognized': '已识别到场景', 'ready': '场景手势已就绪',
    'window_missing': '暂时读不到前台窗口，请在目标应用中重试',
    'no_foreground_application': '暂时读不到前台应用，请重试',
    'no_scene_capability': '该应用尚未识别到支持的场景类型',
    'no_presentation_evidence': '尚未确认进入放映，请先开始放映后重试',
    'non_slide_document': '当前文件不属于演示文稿，未启用放映手势',
    'no_content_evidence': '尚未确认阅读或播放状态，请打开内容后重试',
    'no_playback_controls': '未读到播放控件，请打开播放器控制栏后重试',
    'multiple_content_surfaces': '页面有多个文档或播放器，请点入要操作的内容',
    'page_scan_incomplete': '网页结构暂未完整确认，请点入播放器后重试',
    'no_web_player': '未读到当前页面的播放器，请点入播放器后重试',
    'browser_accessibility_initializing': '浏览器正在准备网页辅助功能信息，稍后自动重试',
    'multiple_players': '当前页面有多个播放器，暂时无法确定操作对象',
    'no_focused_webpage': '焦点未在网页内容内，请点入目标页面',
    'web_player_unconfirmed': '播放器状态尚未确认，请点入播放器后重试',
    'page_loading': '页面仍在加载，请稍后重试',
    'page_url_unavailable': '暂时无法确认当前网页',
    'recognition_timeout': '应用响应较慢，场景识别超时，请稍后重试',
    'focus_validation_timeout': '焦点检查超时，请稍后重试',
    'invalid_metadata': '应用返回的窗口信息不完整，暂时无法识别',
    'text_focus': '焦点在文字输入区域，场景手势暂不接管',
    'unknown_focus': '焦点状态尚未确认，请点入内容区域',
    'focus_missing': '暂时读不到焦点，请点入目标内容',
    'focus_chain_incomplete': '应用未提供完整的焦点归属信息',
    'focus_depth_limit': '焦点层级过深，暂时无法确认归属',
    'unknown_focus_role': '当前控件类型尚无法确认',
    'web_editability_unknown': '网页是否处于文字输入状态尚无法确认',
    'hidden_focus': '焦点所在控件已隐藏，请重新点入目标内容',
    'disabled_focus': '焦点所在控件不可用，请重新点入目标内容',
    'busy_focus': '焦点所在界面仍在加载，请稍后重试',
    'stale_focus': '应用返回了已失效的焦点，请重新点入目标内容',
    'foreign_window_focus': '焦点属于另一个窗口，请重新选择目标窗口',
    'menu_or_sheet_focus': '当前有菜单或弹窗，请关闭后重试',
    'dialog_focus': '当前焦点在对话框内，请关闭后重试',
    'focus_outside_player': '焦点在播放器外，请点入播放器后重试',
    'modal_window': '当前有模态弹窗，请关闭后重试',
    'sheet_open': '当前有附属弹窗，请关闭后重试',
    'dialog_window': '当前为对话框，暂停场景动作',
    'minimized_window': '目标窗口已最小化，请恢复窗口',
    'window_role_unavailable': '暂时读不到有效窗口信息',
    'blocked_window': '当前窗口或控件不适合执行手势',
    'foreground_changed': '前台应用已变化，本次操作已取消',
    'window_changed': '目标窗口已变化，本次操作已取消',
    'focus_changed': '焦点已变化，本次操作已取消',
    'document_changed': '当前文件已变化，本次操作已取消',
    'page_changed': '当前网页或播放器已变化，本次操作已取消',
    'scene_changed': '场景或输入状态已变化，本次操作已取消',
    'target_unavailable': '暂时无法确认目标窗口，本次操作已取消',
    'target_expired': '原生通道或目标记录已失效，请重新触发手势',
    'capture_failed': '无法读取前台应用，请检查辅助功能权限后重试',
    'permission_denied': '辅助功能或按键控制权限尚未生效，请在设置中授权',
    'binding_missing': '当前场景未绑定此手势，可在场景与手势中设置',
    'application_not_configured': '前台应用未添加，继续使用原有手势规则',
    'unconfigured_scene': '该应用尚未配置当前场景',
    'fallback_regular': '当前使用应用常规或全局手势规则',
    'shortcut_recording': '正在录制快捷键，暂不执行场景手势',
    'settings_changed': '绑定配置已变化，请重新触发手势',
    'event_expired': '手势已过期，请重新触发',
    'sentence_changed': '语音输入状态已变化，本次场景操作已取消',
    'speech_busy': '语音任务仍在进行，暂不执行场景动作',
    'phase_blocked': '当前语音处理阶段暂不允许执行场景动作',
    'duplicate_suppressed': '短时间内重复触发，本次已忽略',
    'shortcut_posted': '快捷键已发送；目标应用是否响应尚未验证',
    'key_allocation_failed': '系统未能创建快捷键，请重试',
    'native_exception': '原生操作失败，请稍后重试',
    'channel_start_failed': '原生按键通道启动失败，请重试',
    'channel_timeout': '原生按键通道响应超时，请检查目标应用状态',
    'channel_disconnected': '原生按键通道已断开，请检查目标应用状态',
    'channel_protocol_error': '原生按键通道返回异常，请重试',
    'delivery_unknown': '无法确认快捷键是否已发送，请先查看目标应用状态',
}


def exception_details(exc):
    # Locations and types are useful for debugging; messages/locals may contain
    # file paths, window titles or user content and must never be exported.
    return dict(error_type=type(exc).__name__, error_frames=[
        dict(module=Path(frame.filename).name, function=frame.name, line=frame.lineno)
        for frame in traceback.extract_tb(exc.__traceback__)[-8:]])


def new_trace_id():
    return 's-' + uuid.uuid4().hex[:12]


def reason_message(reason):
    return MESSAGES.get(str(reason), '本次操作未完成，请稍后重试')


def safe_data(value, depth=0):
    """Bound reports and drop raw content even if a future caller adds it."""
    forbidden = {'AXTitle', 'AXDescription', 'AXValue', 'AXSelectedText', 'AXSelectedTextRange',
                 'AXDocument', 'AXURL', 'title', 'text', 'raw', 'url', 'path', 'document',
                 'description', 'window', 'focus', 'web_area', 'player', 'error_message'}
    if depth > 10:
        return None
    if isinstance(value, dict):
        return {str(k)[:64]: safe_data(v, depth + 1) for k, v in list(value.items())[:48]
                if str(k) not in forbidden}
    if isinstance(value, (list, tuple)):
        return [safe_data(item, depth + 1) for item in value[:24]]
    if isinstance(value, str):
        return value[:240]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return None  # AX objects never get repr/str called.


class SceneActionError(RuntimeError):
    def __init__(self, reason, diagnostic=None):
        self.reason = str(reason)
        self.diagnostic = safe_data(diagnostic or {})
        super().__init__(reason_message(reason))


class SceneDiagnostics:
    """Structured scene records in the unified log, plus a small feedback buffer."""
    def __init__(self, path, *, run_id='', writer=None):
        self.path = path
        self.run_id = run_id
        self.writer = writer if writer is not None else RotatingDiagnosticLog(path)
        self._events = deque(maxlen=96)
        self._lock = threading.RLock()

    def record(self, trace_id, stage, reason, **facts):
        try:
            event = safe_data(dict(schema=SCHEMA, run=self.run_id, trace=trace_id,
                time=datetime.now().astimezone().isoformat(timespec='milliseconds'),
                stage=stage, reason=reason, **facts))
            with self._lock:
                self._events.append(event)
                self.writer.record('[SCENE] ' + json.dumps(event, ensure_ascii=False, separators=(',', ':')),
                                   source='scene', run=self.run_id)
            return event
        except Exception:
            return {}  # Logging cannot veto a gesture or replay a shortcut.

    def recent(self, bundle=''):
        with self._lock:
            traces = {event['trace'] for event in self._events if event.get('app') == bundle}
            return [dict(event) for event in self._events if not bundle or event['trace'] in traces][-48:]

    def start_session(self):
        """One background record identifies the code actually loaded this run."""
        try:
            from importlib.metadata import PackageNotFoundError, version
            app_version = "开发版"
            for package in ("mythlink", "proximic-ring"):
                try:
                    app_version = version(package)
                    break
                except PackageNotFoundError:
                    pass
            root = Path(__file__).resolve().parent
            names = ["scene_diagnostics.py", "diagnostic_log.py", "runtime_diagnostics.py", "app_shortcuts.py", "browser_accessibility.py", "mac_shortcut_events.py", "native_access.py", "native_access_worker.py",
                     "ui/app_gesture_controller.py", "ui/ring_gesture_controller.py", "ui/controller.py",
                     "ui/scene_notice_controller.py", "ui/scene_notice_overlay.py", "ui/overlay_stacking.py",
                     "ui/feedback_availability.py", "ui/permission_setup_controller.py", "ui/main.py"]
            names += [str(path.relative_to(root)) for path in sorted((root / "scenes").rglob("*.py"))]
            fingerprints = {name: hashlib.sha256((root / name).read_bytes()).hexdigest()[:16]
                            for name in names if (root / name).is_file()}
            self.record("session-" + self.run_id, "session", "session_started", app_version=app_version,
                        host_pid=os.getpid(), system=platform.system(), system_release=platform.release(),
                        machine=platform.machine(), python=platform.python_version(),
                        packaged=bool(getattr(sys, "frozen", False)), code_fingerprints=fingerprints)
        except Exception:
            pass
