"""Select an IMK source through the configured system shortcut, without AX.

Cross-process TISSelectInputSource can immediately deactivate the new client.
The system shortcut preserves the target application's window focus. Selection
is only a prerequisite: the caller must still verify a fresh IMK client/range.
"""
import ctypes
import threading
import time

from .input_method_install import BUNDLE_ID


class InputSourceUnavailable(RuntimeError):
    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


def _read_on_main_queue(read, *, timeout):
    """Keep TIS on Cocoa's main queue without blocking the gesture deadline.

    HIToolbox can terminate the process (not raise a Python exception) when
    its input-source list is first initialized from a worker thread. Convert
    all CF values to Python values inside this operation, including releases.
    Only short read operations belong here; polling and key events stay on
    the caller. An expired queued operation must not run after a timeout.
    """
    from Foundation import NSOperationQueue, NSThread

    if timeout <= 0:
        raise TimeoutError('输入源查询超时')
    deadline = time.monotonic() + timeout
    if NSThread.isMainThread():
        result = read()
    else:
        done = threading.Event()
        reply = {}

        def perform():
            try:
                if time.monotonic() < deadline:
                    reply['value'] = read()
            except Exception as exc:
                reply['error'] = exc
            finally:
                done.set()

        NSOperationQueue.mainQueue().addOperationWithBlock_(perform)
        if not done.wait(max(0, deadline - time.monotonic())):
            raise TimeoutError('输入源查询超时')
        if 'error' in reply:
            raise reply['error']
        if 'value' not in reply:
            raise TimeoutError('输入源查询超时')
        result = reply['value']
    if time.monotonic() >= deadline:
        raise TimeoutError('输入源查询超时')
    return result


def cycle_to_source(backend, origin, target, *, timeout):
    """At most one source cycle, sharing the caller's activation deadline."""
    deadline = time.monotonic() + timeout

    def read(operation):
        return operation(timeout=max(0, deadline - time.monotonic()))

    allowed = read(backend.sources)
    if target not in allowed:
        raise RuntimeError('Mythlink 输入法尚未启用')
    shortcut = backend.shortcut()
    current = read(backend.current)
    if not current or current not in allowed:
        raise RuntimeError('无法确认当前输入法')
    visited = {current}
    # An already selected but inactive IMK client also needs a real native
    # source transition. A working client never calls this function.
    for _ in range(min(len(allowed), 16)):
        if time.monotonic() >= deadline:
            raise TimeoutError('输入法切换超时')
        if backend.foreground() != origin or read(backend.current) != current:
            raise RuntimeError('前台应用或输入法已变化')
        if time.monotonic() >= deadline:
            raise TimeoutError('输入法切换超时')
        backend.advance(shortcut, origin)
        step_deadline = min(deadline, time.monotonic() + .2)
        while time.monotonic() < step_deadline:
            if backend.foreground() != origin:
                raise RuntimeError('前台应用已变化')
            next_source = read(backend.current)
            if next_source != current:
                if next_source == target:
                    return
                if next_source not in allowed or next_source in visited:
                    raise RuntimeError('系统切换列表中未找到 Mythlink')
                visited.add(next_source)
                current = next_source
                break
            time.sleep(min(.005, max(0, step_deadline - time.monotonic())))
        else:
            raise TimeoutError('系统未确认输入源切换')
    raise RuntimeError('系统切换列表中未找到 Mythlink')


class SystemInputSources:
    """Read TIS on the GUI's Cocoa main queue, even for gesture-worker callers.

    A bare QCoreApplication host does not service the required native input
    source notifications. No source or property CF pointer escapes the queue.
    """
    def __init__(self):
        import Quartz
        self.quartz = Quartz
        self.carbon = ctypes.CDLL('/System/Library/Frameworks/Carbon.framework/Carbon')
        self.cf = ctypes.CDLL('/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation')
        self.carbon.TISCopyCurrentKeyboardInputSource.restype = ctypes.c_void_p
        self.carbon.TISGetInputSourceProperty.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        self.carbon.TISGetInputSourceProperty.restype = ctypes.c_void_p
        self.carbon.TISCreateInputSourceList.argtypes = [ctypes.c_void_p, ctypes.c_bool]
        self.carbon.TISCreateInputSourceList.restype = ctypes.c_void_p
        self.cf.CFStringGetCString.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_long, ctypes.c_uint32]
        self.cf.CFStringGetCString.restype = ctypes.c_bool
        self.cf.CFArrayGetCount.argtypes = [ctypes.c_void_p]
        self.cf.CFArrayGetCount.restype = ctypes.c_long
        self.cf.CFArrayGetValueAtIndex.argtypes = [ctypes.c_void_p, ctypes.c_long]
        self.cf.CFArrayGetValueAtIndex.restype = ctypes.c_void_p
        self.cf.CFBooleanGetValue.argtypes = [ctypes.c_void_p]
        self.cf.CFBooleanGetValue.restype = ctypes.c_bool
        self.cf.CFRelease.argtypes = [ctypes.c_void_p]
        self.cf.CFRelease.restype = None

    def _property(self, source, name):
        key = ctypes.c_void_p.in_dll(self.carbon, name)
        return self.carbon.TISGetInputSourceProperty(source, key)

    def _id(self, source):
        if not source:
            return ''
        value = self._property(source, 'kTISPropertyInputSourceID')
        buffer = ctypes.create_string_buffer(1024)
        if value and self.cf.CFStringGetCString(value, buffer, len(buffer), 0x08000100):
            return buffer.value.decode('utf-8')
        return ''

    def current(self, *, timeout=.2):
        return _read_on_main_queue(self._current, timeout=timeout)

    def _current(self):
        source = self.carbon.TISCopyCurrentKeyboardInputSource()
        try:
            return self._id(source)
        finally:
            if source:
                self.cf.CFRelease(source)

    def sources(self, *, timeout=.2):
        return _read_on_main_queue(self._sources, timeout=timeout)

    def _sources(self):
        sources = self.carbon.TISCreateInputSourceList(None, False)
        result = set()
        if not sources:
            return result
        try:
            for index in range(self.cf.CFArrayGetCount(sources)):
                source = self.cf.CFArrayGetValueAtIndex(sources, index)
                properties = [self._property(source, key) for key in
                              ('kTISPropertyInputSourceIsEnabled', 'kTISPropertyInputSourceIsSelectCapable')]
                if all(value and self.cf.CFBooleanGetValue(value) for value in properties):
                    result.add(self._id(source))
            return result - {''}
        finally:
            self.cf.CFRelease(sources)

    @staticmethod
    def foreground():
        from ...platforms.macos.mac_workspace import frontmost_application
        app = frontmost_application()
        return (str(app.bundleIdentifier() or ''), int(app.processIdentifier())) if app else None

    @staticmethod
    def shortcut():
        from Foundation import NSUserDefaults
        settings = NSUserDefaults.standardUserDefaults().persistentDomainForName_('com.apple.symbolichotkeys') or {}
        binding = (settings.get('AppleSymbolicHotKeys') or {}).get('61') or {}
        value = binding.get('value') or {}
        parameters = list(value.get('parameters') or [])
        if not binding.get('enabled') or value.get('type') != 'standard' or len(parameters) != 3:
            raise InputSourceUnavailable('input_method_source_shortcut_unavailable')
        code, flags = int(parameters[1]), int(parameters[2])
        # Never emit an unmodified printable key, or accept unknown flags.
        if (not 0 <= code <= 127 or code in {54, 55, 56, 58, 59, 60, 61, 62}
                or flags & ~0x1e0000 or not flags & 0x140000):
            raise InputSourceUnavailable('input_method_source_shortcut_unavailable')
        return code, flags

    def advance(self, shortcut, origin):
        q = self.quartz
        if self.shortcut() != shortcut:
            raise InputSourceUnavailable('input_method_source_shortcut_unavailable')
        if not q.CGPreflightPostEventAccess():
            raise InputSourceUnavailable('input_method_source_event_permission')
        if q.CGEventSourceFlagsState(q.kCGEventSourceStateHIDSystemState) & 0x1e0000:
            raise InputSourceUnavailable('input_method_source_modifiers_held')
        code, flags = shortcut
        source = q.CGEventSourceCreate(q.kCGEventSourceStateHIDSystemState)
        if source is None:
            raise RuntimeError('无法创建系统输入事件')
        pressed = []
        key_down = False
        current_flags = 0

        def post(key, down, modifiers):
            event = q.CGEventCreateKeyboardEvent(source, key, down)
            if event is None:
                raise RuntimeError('无法创建系统输入事件')
            q.CGEventSetFlags(event, modifiers)
            q.CGEventSetIntegerValueField(event, q.kCGEventSourceUserData, 0x494d4b54)
            q.CGEventPost(q.kCGHIDEventTap, event)

        try:
            for modifier, mask in ((59, 0x40000), (58, 0x80000), (55, 0x100000), (56, 0x20000)):
                if flags & mask:
                    if self.foreground() != origin:
                        raise RuntimeError('前台应用已变化')
                    current_flags |= mask
                    pressed.append((modifier, mask))
                    post(modifier, True, current_flags)
            time.sleep(.015)
            if self.foreground() != origin:
                raise RuntimeError('前台应用已变化')
            key_down = True
            post(code, True, flags)
            time.sleep(.015)
        finally:
            try:
                if key_down:
                    post(code, False, flags)
            finally:
                for modifier, mask in reversed(pressed):
                    current_flags &= ~mask
                    post(modifier, False, current_flags)


def activate_voice_input_source(pid, *, timeout=.8):
    deadline = time.monotonic() + timeout
    backend = SystemInputSources()
    origin = backend.foreground()
    if not origin or origin[1] != pid:
        raise RuntimeError('前台应用已变化')
    cycle_to_source(backend, origin, BUNDLE_ID + '.dictation',
                    timeout=max(0, deadline - time.monotonic()))
