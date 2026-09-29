"""Memory-only, window-specific ScreenCaptureKit streams. No microphone or files."""
from concurrent.futures import ThreadPoolExecutor
import threading

from .window_selector import same_rect

MAX_STREAMS = 12
PREVIEW_FPS = 30


def dimensions(frame):
    width, height = frame[2:]
    scale = min(720 / max(1, width), 480 / max(1, height), 1)
    return max(2, round(width * scale)), max(2, round(height * scale))


_bridge = None
_bridge_lock = threading.Lock()


def native_bridge():
    global _bridge
    with _bridge_lock:
        if _bridge is not None: return _bridge
        import objc
        import ScreenCaptureKit as SC
        import CoreMedia as CM
        import Quartz as CG
        import dispatch
        from Foundation import NSObject
        from PySide6.QtGui import QImage

        class RingWindowStreamOutput(NSObject, protocols=[objc.protocolNamed("SCStreamOutput")]):
            def stream_didOutputSampleBuffer_ofType_(self, stream, sample, kind):
                if not self.active or kind != SC.SCStreamOutputTypeScreen: return
                try:
                    with objc.autorelease_pool():
                        if not CM.CMSampleBufferIsValid(sample): return
                        attachments = CM.CMSampleBufferGetSampleAttachmentsArray(sample, False)
                        if not attachments or attachments[0].get(SC.SCStreamFrameInfoStatus) != SC.SCFrameStatusComplete:
                            return
                        buffer = CM.CMSampleBufferGetImageBuffer(sample)
                        if buffer is None or CG.CVPixelBufferLockBaseAddress(buffer, 1) != 0: return
                        try:
                            w, h = CG.CVPixelBufferGetWidth(buffer), CG.CVPixelBufferGetHeight(buffer)
                            stride = CG.CVPixelBufferGetBytesPerRow(buffer)
                            if not 0 < w <= 720 or not 0 < h <= 480: return
                            data = CG.CVPixelBufferGetBaseAddress(buffer).as_buffer(stride * h)
                            image = QImage(data, w, h, stride, QImage.Format_ARGB32).copy()
                        finally:
                            CG.CVPixelBufferUnlockBaseAddress(buffer, 1)
                        if self.active and not image.isNull(): self.deliver(image)
                except Exception:
                    if self.active: self.failed()

        _bridge = SC, CM, CG, dispatch, RingWindowStreamOutput
        return _bridge


class WindowCapture:
    """Native lifecycle serialized separately from Qt and the AX worker."""
    def __init__(self):
        self.SC, self.CM, self.CG, self.dispatch, self.Output = native_bridge()
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="RingWindowCapture")
        self._generation = 0
        self._closed = False
        self._streams = {}
        self._retiring = {}
        self._windows = None
        self._desired = []
        self._frame = self._status = None

    def permission(self):
        return bool(self.CG.CGPreflightScreenCaptureAccess())

    def _submit(self, action, *args):
        if self._closed: return
        def task():
            import objc
            with objc.autorelease_pool():
                try: action(*args)
                except Exception:
                    if self._status: self._status("unavailable")
        self._pool.submit(task)

    def start(self, cards, frame, status):
        self._generation += 1
        generation = self._generation
        self._frame, self._status = frame, status
        self._submit(self._start, generation, list(cards))

    def _start(self, generation, cards):
        self._stop_streams()
        if generation != self._generation: return
        self._windows = None
        self._desired = cards[:MAX_STREAMS]
        if not self.permission():
            self._status("permission")
            return
        self._status("loading")
        def catalog(content, error):
            self._submit(self._catalog, generation, content, error)
        self.SC.SCShareableContent.getShareableContentExcludingDesktopWindows_onScreenWindowsOnly_completionHandler_(
            True, True, catalog)

    def _catalog(self, generation, content, error):
        if generation != self._generation: return
        if error is not None or content is None:
            self._status("unavailable" if self.permission() else "permission")
            return
        self._windows = {int(w.windowID()): w for w in content.windows()}
        self._sync(generation, self._desired)

    def update(self, cards):
        self._submit(self._sync, self._generation, list(cards)[:MAX_STREAMS])

    def _sync(self, generation, cards):
        if generation != self._generation: return
        self._desired = cards
        if self._windows is None: return
        ids = {c["id"] for c in cards}
        for identifier in list(self._streams):
            if identifier not in ids: self._stop_one(identifier)
        for card in cards:
            identifier = card["id"]
            if identifier in self._streams: continue
            window = self._windows.get(card.get("number", 0))
            if window is None or window.owningApplication() is None: continue
            rect = window.frame()
            frame = (rect.origin.x, rect.origin.y, rect.size.width, rect.size.height)
            if int(window.owningApplication().processID()) != card["pid"] or not same_rect(frame, card.get("frame")):
                continue
            config = self.SC.SCStreamConfiguration.alloc().init()
            w, h = dimensions(frame)
            config.setWidth_(w); config.setHeight_(h)
            config.setPixelFormat_(self.CG.kCVPixelFormatType_32BGRA)
            config.setMinimumFrameInterval_(self.CM.CMTimeMake(1, PREVIEW_FPS))
            config.setQueueDepth_(3)
            config.setShowsCursor_(False)
            config.setCapturesAudio_(False)
            config.setCaptureMicrophone_(False)
            config.setPreservesAspectRatio_(True)
            config.setIgnoreShadowsSingleWindow_(True)
            config.setIncludeChildWindows_(False)
            content_filter = self.SC.SCContentFilter.alloc().initWithDesktopIndependentWindow_(window)
            stream = self.SC.SCStream.alloc().initWithFilter_configuration_delegate_(content_filter, config, None)
            output = self.Output.alloc().init()
            output.active = True
            deliver, report = self._frame, self._status
            output.deliver = lambda image, key=identifier, epoch=generation: (
                deliver(key, image) if epoch == self._generation else None)
            output.failed = lambda epoch=generation: report("unavailable") if epoch == self._generation else None
            queue = self.dispatch.dispatch_queue_create(b"com.proximic.window-preview", None)
            ok, error = stream.addStreamOutput_type_sampleHandlerQueue_error_(
                output, self.SC.SCStreamOutputTypeScreen, queue, None)
            if not ok:
                output.active = False
                continue
            self._streams[identifier] = stream, output, queue
            def started(error, epoch=generation, report=report):
                if error is not None and epoch == self._generation:
                    report("unavailable" if self.permission() else "permission")
            stream.startCaptureWithCompletionHandler_(started)
        if not self._streams: self._status("unavailable")

    def _stop_one(self, identifier):
        record = self._streams.pop(identifier)
        stream, output, _ = record
        output.active = False
        key = id(stream)
        self._retiring[key] = record  # Keep callbacks/queue alive until native stop completes.
        def stopped(error):
            self._retiring.pop(key, None)
        stream.stopCaptureWithCompletionHandler_(stopped)

    def _stop_streams(self):
        for identifier in list(self._streams): self._stop_one(identifier)

    def stop(self):
        self._generation += 1  # Drop callbacks immediately, before native teardown.
        self._submit(self._stop_streams)

    def close(self):
        self.stop()
        self._closed = True
        self._pool.shutdown(wait=False)
