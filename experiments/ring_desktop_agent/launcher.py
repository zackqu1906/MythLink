"""Launch the native request window and controller as one owned session."""
from __future__ import annotations

import argparse
import contextlib
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import plistlib
import shutil
import signal
import subprocess
import sys

SOURCE = Path(__file__).resolve().parent
REPO = SOURCE.parents[1]
DEFAULT_RUNTIME = REPO / '.cache/ring-agent-demo'


def parser():
    p = argparse.ArgumentParser(description='Mythlink 戒指 → 桌面 Agent 独立测试程序（macOS）')
    p.add_argument('--output-dir', type=Path,
                   help='可选保存位置偏好；不传则按任务要求或应用当前保存位置决定，不限制其他目录')
    p.add_argument('--runtime-dir', type=Path, default=DEFAULT_RUNTIME,
                   help='状态、日志、音频和编译产物目录')
    p.add_argument('--codex', default=os.environ.get('RING_AGENT_CODEX'),
                   help='Codex 可执行文件路径，默认使用已安装 Codex.app / ChatGPT.app 中的版本')
    p.add_argument('--no-ring', action='store_true', help='仅测试窗口文字输入，不连接戒指')
    p.add_argument('--duration', type=int, default=0, help='运行秒数，默认 0 表示直到关闭窗口或 Ctrl+C')
    p.add_argument('--check', action='store_true', help='仅检查环境，不启动 Agent / 蓝牙 / 窗口')
    return p


def find_codex(explicit=None):
    if explicit:
        candidate = shutil.which(explicit) or str(Path(explicit).expanduser())
        return candidate if os.access(candidate, os.X_OK) else None
    for candidate in ('/Applications/Codex.app/Contents/Resources/codex',
                      '/Applications/ChatGPT.app/Contents/Resources/codex',
                      shutil.which('codex')):
        if candidate and os.access(candidate, os.X_OK):
            return candidate
    return None


def preflight(args):
    errors = []
    if sys.platform != 'darwin':
        errors.append('此原生窗口测试目前仅支持 macOS。')
    if not find_codex(args.codex):
        errors.append('未找到 Codex 可执行文件；可用 --codex 指定。')
    if not shutil.which('xcrun'):
        errors.append('缺少 Xcode Command Line Tools（xcrun / swiftc）。')
    if args.duration < 0:
        errors.append('--duration 必须大于或等于 0。')
    if not args.no_ring:
        missing = [name for name in ('numpy', 'torch', 'bleak', 'cryptography', 'websocket')
                   if importlib.util.find_spec(name) is None]
        if missing:
            errors.append('Python 环境缺少依赖：' + ', '.join(missing) + '；请使用 Mythlink 的 .runtime/venv。')
        settings_file = Path.home()/'Library/Preferences/com.ProxiMic.ProxiMic Voice.plist'
        try:
            settings = plistlib.loads(settings_file.read_bytes())
            if settings.get('asr.backend') != 'volcengine' or not settings.get('asr.volcengineApiKey'):
                errors.append('请先在 Mythlink 配置可用的火山引擎语音识别服务。')
            if not settings.get('ring.selector'):
                errors.append('请先在 Mythlink 选择要连接的戒指。')
        except (OSError, ValueError, plistlib.InvalidFileException):
            errors.append('未找到有效的 Mythlink 戒指 / ASR 设置。')
    return errors


def build_window(runtime):
    bundle = runtime/'RingAgentDemo.app'
    executable = bundle/'Contents/MacOS/RingAgentDemo'
    executable.parent.mkdir(parents=True, exist_ok=True)
    (bundle/'Contents/Info.plist').write_bytes(plistlib.dumps({
        'CFBundleExecutable': 'RingAgentDemo',
        'CFBundleIdentifier': 'local.mythlink.ring-agent-demo',
        'CFBundleName': 'Mythlink 戒指 Agent 测试',
        'CFBundlePackageType': 'APPL',
        'NSHighResolutionCapable': True,
    }))
    source = SOURCE/'Console.swift'
    if not executable.exists() or executable.stat().st_mtime < source.stat().st_mtime:
        print('正在编译请求窗口…', flush=True)
        subprocess.run(['xcrun', 'swiftc', str(source), '-o', str(executable),
                        '-module-cache-path', str(runtime/'swift-cache'),
                        '-framework', 'AppKit'], check=True)
    return executable


def stop_window(proc):
    if proc and proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=3)


def main(argv=None):
    args = parser().parse_args(argv)
    errors = preflight(args)
    if args.check:
        print(json.dumps({'ready': not errors, 'python': sys.executable,
                          'codex': find_codex(args.codex),
                          'outputDirectory': str(args.output_dir.expanduser().resolve()) if args.output_dir else None,
                          'errors': errors}, ensure_ascii=False, indent=2))
        return int(bool(errors))
    if errors:
        print('\n'.join(errors), file=sys.stderr)
        return 1

    # Fixed lock location prevents a second instance even with --runtime-dir.
    DEFAULT_RUNTIME.mkdir(parents=True, exist_ok=True)
    with (DEFAULT_RUNTIME/'controller.lock').open('a+') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print('已有戒指 Agent 测试在运行；请先关闭原窗口或按 Ctrl+C。', file=sys.stderr)
            return 1
        runtime = args.runtime_dir.expanduser().resolve()
        runtime.mkdir(parents=True, exist_ok=True)
        commands = runtime/'commands'
        commands.mkdir(exist_ok=True)
        for stale in commands.glob('*.json'):
            stale.unlink()
        executable = build_window(runtime)
        import flow
        flow.ROOT = runtime
        app = flow.Flow(output_dir=args.output_dir, codex_path=find_codex(args.codex),
                        with_ring=not args.no_ring, duration=args.duration)
        previous_signals = {sig: signal.signal(sig, lambda *_: app.stop.set())
                            for sig in (signal.SIGINT, signal.SIGTERM)}
        window = None
        print('保存位置偏好：', app.output_dir or '未指定，按任务要求或应用当前保存位置决定')
        print('运行日志：', runtime/'controller.log')
        print('退出：关闭请求窗口 / 结束测试 / 此终端 Ctrl+C。')
        if not args.no_ring:
            print('请保持 Mythlink 主应用断开戒指，避免两个程序同时连接。')
        print('正在启动窗口与 Agent…', flush=True)
        try:
            with (runtime/'controller.log').open('a', buffering=1) as log:
                window = subprocess.Popen([str(executable), str(runtime)], stdout=log, stderr=log)
                app.ui_process = window
                with contextlib.redirect_stdout(log):
                    app.run()
            return 0
        except Exception as exc:
            print(f'启动或运行失败：{type(exc).__name__}: {exc}', file=sys.stderr)
            return 1
        finally:
            stop_window(window)
            if not app.events.closed:
                app.events.close()
            for sig, handler in previous_signals.items():
                signal.signal(sig, handler)
            print('测试已结束。', flush=True)


if __name__ == '__main__':
    raise SystemExit(main())
