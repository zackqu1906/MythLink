"""Freeze the submission-time window; the Agent decides whether to read it."""
from __future__ import annotations
from datetime import datetime, timezone
import time

from .capture_backend import _await, CapturePermissionError

# Bound the actual frame acquisition, not PNG encoding or model latency.
MAX_CAPTURE_DELAY = 1.0


def needs_context(text, attachments=(), mode='smart'):
    """Capture locally without interpreting words; smart images are read on demand."""
    if any(a.get('kind') == 'image' for a in attachments): return False
    return mode in ('smart', 'always')


def current_target(excluded_ids):
    """Read front-to-back WindowServer order without activating any window.

    Agent/IME floating surfaces are not the page the user is referring to.
    Look through them to the frontmost visible normal window at submission,
    independent of the old focus-return target or invocation timestamp.
    """
    import Quartz as CG
    rows = CG.CGWindowListCopyWindowInfo(
        CG.kCGWindowListOptionOnScreenOnly | CG.kCGWindowListExcludeDesktopElements, 0) or []
    for row in rows:
        window_id = int(row.get(CG.kCGWindowNumber, 0))
        pid = int(row.get(CG.kCGWindowOwnerPID, 0))
        bounds = row.get(CG.kCGWindowBounds, {})
        if (window_id <= 0 or pid <= 0 or window_id in excluded_ids
                or row.get(CG.kCGWindowLayer) != 0 or row.get(CG.kCGWindowAlpha, 1) <= 0
                or bounds.get('Width', 0) <= 0 or bounds.get('Height', 0) <= 0):
            continue
        return dict(pid=pid, window_id=window_id)
    raise ValueError('当前没有可截图的窗口')


def capture_window(target, cancelled, *, deadline=None):
    """Capture this exact window's latest content and refresh its metadata."""
    if deadline is None:deadline=time.monotonic()+MAX_CAPTURE_DELAY
    def remaining():
        delay=deadline-time.monotonic()
        if delay<=0:raise TimeoutError('截图超过发送时限，未使用稍后的画面')
        return delay
    remaining()
    import objc
    import Quartz as CG
    import ScreenCaptureKit as SC
    from Foundation import NSMutableData
    from PySide6.QtGui import QImage
    if not CG.CGPreflightScreenCaptureAccess():
        raise CapturePermissionError('自动截图需要屏幕录制权限')
    with objc.autorelease_pool():
        content = _await(lambda cb: SC.SCShareableContent
            .getShareableContentExcludingDesktopWindows_onScreenWindowsOnly_completionHandler_(
                False, True, cb), cancelled, timeout=remaining())
        window = next((w for w in content.windows() if int(w.windowID()) == int(target['window_id'])
                       and w.owningApplication() is not None
                       and int(w.owningApplication().processID()) == int(target['pid'])), None)
        if window is None:
            raise ValueError('截图目标已关闭或不可见')
        # Titles and page contents may change between turns (or during input).
        # They describe the new image; they are not a writeback ownership guard.
        app = window.owningApplication()
        target.update(window_title=str(window.title() or ''),
                      app=str(app.applicationName() or ''), bundle=str(app.bundleIdentifier() or ''))
        target.pop('document_url', None)  # A previous page's URL is not current evidence.
        content_filter = SC.SCContentFilter.alloc().initWithDesktopIndependentWindow_(window)
        rect = window.frame()
        scale = float(content_filter.pointPixelScale())
        width, height = round(rect.size.width*scale), round(rect.size.height*scale)
        if width <= 0 or height <= 0:
            raise ValueError('原窗口尺寸无效')
        # Preserve readable text while bounding unexpectedly large windows.
        ratio = min(1, 4096/max(width,height))
        config = SC.SCStreamConfiguration.alloc().init()
        config.setWidth_(max(1,round(width*ratio)))
        config.setHeight_(max(1,round(height*ratio)))
        config.setShowsCursor_(False)
        config.setCapturesAudio_(False)
        cg_image = _await(lambda cb: SC.SCScreenshotManager
            .captureImageWithFilter_configuration_completionHandler_(content_filter,config,cb),
            cancelled,timeout=remaining())
        remaining()  # Never label a delayed system callback as a send-time frame.
        target['captured_at']=datetime.now(timezone.utc).isoformat()
        target['captured_monotonic']=time.monotonic()
        data = NSMutableData.data()
        dest = CG.CGImageDestinationCreateWithData(data, 'public.png', 1, None)
        CG.CGImageDestinationAddImage(dest, cg_image, None)
        if not CG.CGImageDestinationFinalize(dest):
            raise RuntimeError('无法保存窗口截图')
        image = QImage.fromData(bytes(data), 'PNG')
        if image.isNull(): raise RuntimeError('窗口截图为空')
        return image
