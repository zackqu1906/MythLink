"""Ventura capture keeps the same window ownership and overlay boundaries."""
from contextlib import nullcontext
import sys
import threading
from types import SimpleNamespace as NS

import pytest
from PySide6.QtGui import QImage

from proximic_ring.agent_workflow.capture import auto_context, capture_backend
from proximic_ring.desktop.window_capture import WindowCapture


def test_ventura_screen_capture_excludes_overlays_and_keeps_display_coordinates(monkeypatch):
    calls = []
    cg = NS(CGPreflightScreenCaptureAccess=lambda: True,
            kCGWindowListOptionOnScreenOnly=1, kCGWindowImageBestResolution=8,
            CGWindowListCreate=lambda *_: [21, 15, 7, 2],
            CGWindowListCreateImageFromArray=lambda *args: calls.append(args) or object())
    monkeypatch.setitem(sys.modules, 'Quartz', cg)
    displays = [NS(frame=lambda: NS(origin=NS(x=-800, y=0), size=NS(width=800, height=600))),
                NS(frame=lambda: NS(origin=NS(x=0, y=0), size=NS(width=1200, height=800)))]
    content = NS(displays=lambda: displays)
    sc = NS(SCShareableContent=NS(
        getShareableContentExcludingDesktopWindows_onScreenWindowsOnly_completionHandler_=
        lambda a, b, callback: callback(content, None)))
    monkeypatch.setitem(sys.modules, 'ScreenCaptureKit', sc)
    monkeypatch.setitem(sys.modules, 'objc', NS(autorelease_pool=nullcontext))
    monkeypatch.setattr(capture_backend, '_image_from_cg',
                        lambda _: QImage(100, 100, QImage.Format_RGB32))
    specs = [{'geometry': (-800, 0, 800, 600), 'scale': 1},
             {'geometry': (0, 0, 1200, 800), 'scale': 2}]
    images = capture_backend.capture_screens(specs, {21, 7}, threading.Event())
    assert calls == [(((-800, 0), (800, 600)), [15, 2], 8),
                     (((0, 0), (1200, 800)), [15, 2], 8)]
    assert [(image.width(), image.height()) for image in images] == [(800, 600), (2400, 1600)]
    cancelled = threading.Event(); cancelled.set()
    with pytest.raises(InterruptedError):
        capture_backend.capture_screens(specs, {21, 7}, cancelled)
    assert len(calls) == 2
    displays.clear()
    with pytest.raises(RuntimeError, match='显示器布局已变化'):
        capture_backend.capture_screens(specs, {21, 7}, threading.Event())
    assert len(calls) == 2
    cg.CGPreflightScreenCaptureAccess = lambda: False
    with pytest.raises(capture_backend.CapturePermissionError):
        capture_backend.capture_screens(specs, {21, 7}, threading.Event())
    assert len(calls) == 2


def test_ventura_auto_context_captures_only_verified_window_and_refreshes_metadata(monkeypatch):
    calls = []
    owner = NS(processID=lambda: 111, applicationName=lambda: 'Fixture',
               bundleIdentifier=lambda: 'test.fixture')
    window = NS(windowID=lambda: 42, owningApplication=lambda: owner,
                title=lambda: 'New page')
    content = NS(windows=lambda: [window])
    sc = NS(SCShareableContent=NS(
        getShareableContentExcludingDesktopWindows_onScreenWindowsOnly_completionHandler_=
        lambda a, b, callback: callback(content, None)))
    cg = NS(CGPreflightScreenCaptureAccess=lambda: True, CGRectNull='window_bounds',
            kCGWindowListOptionIncludingWindow=8, kCGWindowImageBoundsIgnoreFraming=1,
            kCGWindowImageBestResolution=8,
            CGWindowListCreateImage=lambda *args: calls.append(args) or object())
    monkeypatch.setitem(sys.modules, 'ScreenCaptureKit', sc)
    monkeypatch.setitem(sys.modules, 'Quartz', cg)
    monkeypatch.setitem(sys.modules, 'objc', NS(autorelease_pool=nullcontext))
    monkeypatch.setattr(auto_context, '_image_from_cg',
                        lambda _: QImage(5000, 3000, QImage.Format_RGB32))
    target = {'window_id': 42, 'pid': 111, 'document_url': 'file:///old-page'}
    image = auto_context.capture_window(target, threading.Event())
    assert calls == [('window_bounds', 8, 42, 9)]
    assert image.width() == 4096 and abs(image.height() - 4096 * 3 / 5) < 1
    assert target['window_title'] == 'New page' and target['bundle'] == 'test.fixture'
    assert 'document_url' not in target and target['captured_at'] and target['captured_monotonic']
    # A reused window ID from another process is never a valid capture target.
    with pytest.raises(ValueError, match='已关闭或不可见'):
        auto_context.capture_window({'window_id': 42, 'pid': 222}, threading.Event())
    assert len(calls) == 1


@pytest.mark.parametrize('new_options', [False, True])
def test_window_preview_only_sets_options_supported_by_running_macos(new_options):
    assigned = {}
    methods = ['Width', 'Height', 'PixelFormat', 'MinimumFrameInterval', 'QueueDepth',
               'ShowsCursor', 'CapturesAudio']
    optional = {'CaptureMicrophone': False, 'PreservesAspectRatio': True,
                'IgnoreShadowsSingleWindow': True, 'IncludeChildWindows': False}
    config = NS(**{'set' + name + '_': lambda value, key=name: assigned.update({key: value})
                   for name in methods + (list(optional) if new_options else [])})
    allocated = lambda value: NS(alloc=lambda: NS(init=lambda: value))
    started = []
    stream = NS(addStreamOutput_type_sampleHandlerQueue_error_=lambda *args: (True, None),
                startCaptureWithCompletionHandler_=lambda callback: started.append(True))
    frame = NS(origin=NS(x=0, y=0), size=NS(width=640, height=480))
    window = NS(frame=lambda: frame, owningApplication=lambda: NS(processID=lambda: 111))
    capture = WindowCapture.__new__(WindowCapture)
    capture.SC = NS(SCStreamConfiguration=allocated(config), SCStreamOutputTypeScreen=0,
                    SCContentFilter=NS(alloc=lambda: NS(initWithDesktopIndependentWindow_=lambda _: object())),
                    SCStream=NS(alloc=lambda: NS(initWithFilter_configuration_delegate_=lambda *args: stream)))
    capture.CM = NS(CMTimeMake=lambda *args: args)
    capture.CG = NS(kCVPixelFormatType_32BGRA=1)
    capture.dispatch = NS(dispatch_queue_create=lambda *args: object())
    capture.Output = allocated(NS())
    capture._generation = 1
    capture._windows = {42: window}
    capture._streams = {}
    capture._frame = lambda *args: None
    capture._status = lambda status: pytest.fail(status)
    capture._sync(1, [dict(id='fixture', number=42, pid=111, frame=(0, 0, 640, 480))])
    assert started == [True] and 'fixture' in capture._streams
    assert assigned['CapturesAudio'] is False
    assert {key: assigned[key] for key in optional if key in assigned} == (optional if new_options else {})
