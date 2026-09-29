"""Bounded native selection diagnostic. Only the disposable local Safari page."""
import sys, time, subprocess
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))
import AppKit, ApplicationServices as AX
from Foundation import NSRunLoop, NSDate
from proximic_ring.native_access import NativeAccessChannel

def get(e, a):
    rc, value = AX.AXUIElementCopyAttributeValue(e, a, None)
    if rc: raise RuntimeError(f'{a}: {rc}')
    return value

def verify():
    NSRunLoop.currentRunLoop().runUntilDate_(NSDate.dateWithTimeIntervalSinceNow_(.01))
    app = AppKit.NSWorkspace.sharedWorkspace().frontmostApplication()
    assert app and app.bundleIdentifier() == 'com.apple.Safari', 'Safari must be frontmost'
    pid = int(app.processIdentifier())
    element = AX.AXUIElementCreateApplication(pid)
    assert get(get(element, 'AXFocusedWindow'), 'AXTitle') == 'Ring 本地编辑诊断（无网络）'
    focus = get(element, 'AXFocusedUIElement')
    assert get(focus, 'AXRole') in ('AXTextField', 'AXTextArea')
    assert get(focus, 'AXValue') == '原文内容删除全部内容。', 'Only the disposable test field is allowed'
    return pid, focus

def selection(focus):
    ok, r = AX.AXValueGetValue(get(focus, 'AXSelectedTextRange'), AX.kAXValueCFRangeType, None)
    assert ok
    return list(r)

channel=NativeAccessChannel()
if '--without-refresh' in sys.argv:
    def start_unpatched():
        if channel._process is None:
            channel._process = subprocess.Popen([sys.executable, '-c',
                'import proximic_ring.wechat_native_keys as k; k._refresh_workspace=lambda:None; '
                'from proximic_ring.native_access_worker import main; main()'],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0)
    channel._start=start_unpatched
try:
    pid, focus=verify()
    print('before', selection(focus), flush=True)
    channel.call('sentence_key', bundle='com.apple.Safari', pid=pid, command='select_to_start', count=0, event_tag=981230)
    NSRunLoop.currentRunLoop().runUntilDate_(NSDate.dateWithTimeIntervalSinceNow_(.15))
    _, focus=verify()
    print('after', selection(focus), flush=True)
finally:
    channel.close()
