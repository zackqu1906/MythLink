"""macOS 13 screen capture compatibility; newer systems use ScreenCaptureKit."""
from __future__ import annotations


def screenshot_manager(screen_capture_kit):
    return getattr(screen_capture_kit, 'SCScreenshotManager', None)


def configure_window_preview(configuration):
    # Audio capture exists on 13. Microphone capture does not exist before 15;
    # omitting that unavailable setter cannot enable microphone capture on 13.
    configuration.setCapturesAudio_(False)
    for selector, value in (
        ('setCaptureMicrophone_', False),          # 15.0
        ('setPreservesAspectRatio_', True),        # 14.0
        ('setIgnoreShadowsSingleWindow_', True),   # 14.0
        ('setIncludeChildWindows_', False),        # 14.2
    ):
        method = getattr(configuration, selector, None)
        if method is not None:
            method(value)


def legacy_window_image(quartz, window_id):
    # The window catalog/PID has already been checked by the caller. Including
    # exactly this ID captures it even when the Agent's panel is in front.
    return quartz.CGWindowListCreateImage(
        quartz.CGRectNull, quartz.kCGWindowListOptionIncludingWindow, int(window_id),
        quartz.kCGWindowImageBoundsIgnoreFraming | quartz.kCGWindowImageBestResolution)


def legacy_screen_image(quartz, geometry, excluded_ids):
    # Keep WindowServer's front-to-back order and the desktop background.
    # The loading cover and Agent windows must not darken or obscure the frame.
    rows = quartz.CGWindowListCopyWindowInfo(quartz.kCGWindowListOptionOnScreenOnly, 0) or []
    windows = [int(row[quartz.kCGWindowNumber]) for row in rows
               if int(row[quartz.kCGWindowNumber]) not in excluded_ids]
    if not windows:
        raise RuntimeError('当前没有可截图的窗口')
    x, y, width, height = geometry
    return quartz.CGWindowListCreateImageFromArray(
        quartz.CGRectMake(x, y, width, height), windows, quartz.kCGWindowImageBestResolution)


def check_capture(cancelled, deadline):
    import time
    if cancelled.is_set():
        raise InterruptedError('截图已取消')
    if time.monotonic() >= deadline:
        raise TimeoutError('截图超过发送时限，未使用稍后的画面')
