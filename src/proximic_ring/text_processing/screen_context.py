"""Shared raw window/OCR capture and local layout helpers for context preparation."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError
from datetime import datetime, timezone
import re
import sys
import threading
import time
import uuid

CHAT_APPS = frozenset({"com.tencent.xinWeChat", "com.tencent.WeChat", "com.tencent.qq",
                       "com.tinyspeck.slackmacgap", "com.electron.lark", "com.bytedance.feishu"})
WECHAT_APPS = frozenset({"com.tencent.xinWeChat", "com.tencent.WeChat"})
MAX_CONTEXT_CHARS = 2400


class ScreenContext(dict):
    """Model-visible text with local-only evidence outside the JSON mapping."""

    def __init__(self, context, *, evidence=None):
        super().__init__(context)
        self.evidence = evidence or {}


def unavailable(reason, application=""):
    return {"source": "screen_ocr", "status": "unavailable", "reason": str(reason),
            "application": application, "coverage": "visible_only", "messages": []}


def resolve_context(value):
    if hasattr(value, "result"):
        try:
            return value.result(timeout=4.0)
        except TimeoutError:
            return unavailable("截图上文读取超时")
        except Exception as exc:
            return unavailable(str(exc))
    if isinstance(value, ScreenContext):
        return value
    return dict(value) if isinstance(value, dict) else unavailable("未采集截图")


def filter_ocr_lines(lines, *, application, sidebar=0.0, history_bottom=1.0):
    """Normalized top-left boxes; exclude contacts, header controls and composer.

    Speaker hints come from bubble pixels, not from a language model guessing
    authorship. Unknown layouts retain unknown speakers and explicit coverage.
    """
    chat = application in CHAT_APPS
    title = ""
    content = []
    observations = []
    for line in sorted(lines, key=lambda item: (item["box"][1], item["box"][0])):
        text = str(line.get("text", "")).strip()
        x, y, width, height = line["box"]
        observation = dict(line, disposition="passed_filter")
        observations.append(observation)
        if not text or not re.search(r"[\w\u3400-\u9fff]", text) or line.get("confidence", 1) < .25:
            observation["disposition"] = "empty_or_symbols_or_low_confidence"
            continue
        if chat and x < sidebar:
            observation["disposition"] = "sidebar"
            continue
        if chat and y < .075:
            observation["disposition"] = "header"
            if not title and x < .8 and len(text) <= 80:
                title = text
                observation["disposition"] = "conversation_title"
            continue
        if chat and (y + height > history_bottom or y < .075):
            observation["disposition"] = "outside_history"
            continue
        if re.fullmatch(r"(?:昨天\s*)?\d{1,2}:\d{2}|\d{1,2}[/-]\d{1,2}(?:\s+\d{1,2}:\d{2})?", text):
            observation["disposition"] = "timestamp"
            continue
        speaker = str(line.get("speaker", "unknown")) if chat else "unknown"
        if content:
            previous = content[-1]
            px, py, pw, ph = previous["box"]
            gap = y - (py + ph)
            if (speaker == previous["speaker"] and -.008 <= gap < max(.015, ph * .75)
                    and abs(x - px) < .04):
                previous["text"] += "\n" + text
                previous["box"] = [min(x, px), py, max(x + width, px + pw) - min(x, px), y + height - py]
                previous["ocr_lines"].append(line)
                observation["disposition"] = "merged"
                continue
        content.append({"speaker": speaker, "text": text, "box": [x, y, width, height], "ocr_lines": [line]})
    kept, kept_lines, remaining = [], [], MAX_CONTEXT_CHARS
    for message in reversed(content[-16:]):
        text = message["text"]
        if len(text) > remaining:
            if not kept:
                kept.append({"speaker": message["speaker"], "text": text[-remaining:], "truncated": True})
                # Keep the same suffix in the LLM's positional evidence. A box
                # still describes the original OCR line, including when cut.
                tail, budget = [], remaining
                for line in reversed(message["ocr_lines"]):
                    fragment = str(line["text"]).strip()[-budget:]
                    tail.append(dict(line, text=fragment))
                    budget -= len(fragment) + 1  # Newline between OCR lines.
                    if budget <= 0:
                        break
                kept_lines.append(list(reversed(tail)))
            break
        kept.append({"speaker": message["speaker"], "text": text})
        kept_lines.append(message["ocr_lines"])
        remaining -= len(text)
    return ScreenContext({"source": "screen_ocr", "status": "ok" if kept else "empty", "application": application,
            "coverage": "visible_only", "conversation_title": title,
            "speaker_source": "bubble_geometry" if application in WECHAT_APPS else "unknown",
            "messages": list(reversed(kept))}, evidence={"ocr_lines": observations,
            "message_ocr_lines": list(reversed(kept_lines)),
            "blocks_before_limit": len(content), "blocks_after_limit": len(kept)})


def _await(start, timeout=3.0):
    done, result = threading.Event(), []
    def completed(value, error):
        result.extend((value, error)); done.set()
    start(completed)
    if not done.wait(timeout):
        raise TimeoutError("截图接口超时")
    if result[1] is not None or result[0] is None:
        raise RuntimeError("截图失败，请检查屏幕录制权限")
    return result[0]


def recognize_image(image, application):
    """Recognize the entire window without discarding text in manual sessions."""
    import Quartz as CG
    import Vision
    import numpy as np
    width, height = CG.CGImageGetWidth(image), CG.CGImageGetHeight(image)
    pixels = bytearray(width * height * 4)
    canvas = CG.CGBitmapContextCreate(pixels, width, height, 8, width * 4,
                                     CG.CGColorSpaceCreateDeviceRGB(), CG.kCGImageAlphaPremultipliedLast | CG.kCGBitmapByteOrder32Big)
    CG.CGContextDrawImage(canvas, CG.CGRectMake(0, 0, width, height), image)
    rgba = np.frombuffer(pixels, dtype=np.uint8).reshape(height, width, 4)
    # ScreenCaptureKit can succeed while returning a fully transparent image
    # for a hidden/non-shareable window. This is capture failure, not a window
    # without text. Do not confuse opaque black content with this case.
    if not np.any(rgba[:, :, 3]):
        return ScreenContext({**unavailable("窗口截图全透明，未取得实际画面，OCR 未执行", application),
            "processing": "raw", "error_code": "transparent_window_capture"}, evidence={
                "ocr_lines": [], "ocr_scope": "target_window", "image_validity": "fully_transparent"})
    rgb = rgba[:, :, :3]
    sidebar, bottom, layout_error = 0.0, 1.0, ""
    if application in CHAT_APPS:
        # A persistent vertical panel border, measured across many rows, avoids
        # hard-coding WeChat's resizable contact-list width.
        band = rgb[int(height * .12):int(height * .72)].astype(np.int16)
        delta = np.mean(np.abs(np.diff(band, axis=1)), axis=2)
        strength = np.mean(delta > 8, axis=0)
        lo, hi = int(width * .15), int(width * .55)
        column = lo + int(np.argmax(strength[lo:hi]))
        if strength[column] < .5:
            layout_error = "无法可靠定位聊天区域，未使用整窗文字"
        sidebar = (column + 2) / width
        body = rgb[:, min(width - 1, column + 40):int(width * .97)].astype(np.int16)
        horizontal = np.mean(np.mean(np.abs(np.diff(body, axis=0)), axis=2) > 6, axis=1)
        lo, hi = int(height * .50), int(height * .90)
        candidates = np.flatnonzero(horizontal[lo:hi] > .55)
        # This bound excludes the input draft even if the rounded border is
        # too faint. Unsupported layouts are explicitly marked below.
        bottom = (lo + int(candidates[-1])) / height if len(candidates) else .76
    request = Vision.VNRecognizeTextRequest.alloc().init()
    request.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
    request.setRecognitionLanguages_(["zh-Hans", "en-US"])
    request.setUsesLanguageCorrection_(True)
    # Full-window OCR is evidence shared by all three modes. Region selection
    # and text exclusions derive separate inputs without changing this evidence.
    handler = Vision.VNImageRequestHandler.alloc().initWithCGImage_options_(image, {})
    ok, error = handler.performRequests_error_([request], None)
    if not ok:
        raise RuntimeError(f"OCR 失败：{error}")
    lines = []
    for observation in request.results() or []:
        candidates = observation.topCandidates_(1)
        if not candidates:
            continue
        candidate, box = candidates[0], observation.boundingBox()
        x, y, w, h = box.origin.x, 1 - box.origin.y - box.size.height, box.size.width, box.size.height
        speaker = "unknown"
        if application in WECHAT_APPS and y > .075:
            # Sample just outside the text, inside the bubble's padding.
            px, py = max(0, int(x * width) - 8), min(height - 1, int((y + h / 2) * height))
            red, green, blue = (int(v) for v in rgb[py, px])
            if green > red + 12 and green > blue + 12:
                speaker = "self"
            elif abs(red - green) < 8 and abs(green - blue) < 8 and 190 < red < 245:
                speaker = "other"
        lines.append({"text": str(candidate.string()), "confidence": float(candidate.confidence()),
                      "box": [x, y, w, h], "speaker": speaker})
    return ScreenContext({
        "source": "screen_ocr", "status": "ok" if lines else "empty",
        "application": application, "coverage": "visible_only", "processing": "raw",
        "layout": {"sidebar": sidebar, "history_bottom": bottom}, "layout_error": layout_error,
    }, evidence={"ocr_lines": lines, "ocr_scope": "target_window"})


def filter_context(context):
    """Derive automatic model context without mutating the shared raw capture.

    Older callers already supply filtered contexts. Local OCR boxes, confidence
    and PNG bytes stay outside the model-visible mapping in either case.
    """
    if context.get("processing") != "raw":
        return context
    evidence = dict(getattr(context, "evidence", {}))
    application = context.get("application", "")
    if context.get("layout_error") or context.get("status") == "unavailable":
        filtered = unavailable(context.get("layout_error") or context.get("reason", "OCR 不可用"), application)
    else:
        layout = context.get("layout") or {}
        filtered = filter_ocr_lines(evidence.get("ocr_lines", []), application=application,
                                    sidebar=layout.get("sidebar", 0), history_bottom=layout.get("history_bottom", 1))
        evidence.update({key: value for key, value in filtered.evidence.items() if key != "ocr_lines"})
        evidence["ocr_filter_lines"] = filtered.evidence["ocr_lines"]
    metadata = {key: value for key, value in context.items() if key not in {"status", "reason", "processing", "layout_error"}}
    return ScreenContext({**metadata, **filtered, "processing": "filtered"}, evidence=evidence)


def capture_context(target):
    application = str(getattr(target, "process_name", "") or "")
    if sys.platform != "darwin" or not application:
        return unavailable("当前平台或目标不支持截图上文", application)
    import objc
    import Quartz as CG
    import ScreenCaptureKit as SC
    from AppKit import NSWorkspace
    from ..platforms.macos import screen_capture as compatibility
    started = time.perf_counter()
    with objc.autorelease_pool():
        if not CG.CGPreflightScreenCaptureAccess():
            return unavailable("需要屏幕录制权限", application)
        front = NSWorkspace.sharedWorkspace().frontmostApplication()
        if front is None or application not in {str(front.bundleIdentifier()), str(front.localizedName())}:
            return unavailable("前台应用已变化", application)
        pid, bundle = int(front.processIdentifier()), str(front.bundleIdentifier())
        application_name = str(front.localizedName() or "")
        if getattr(target, "process_id", 0) and int(target.process_id) != pid:
            return unavailable("目标应用进程已变化", bundle)
        windows = CG.CGWindowListCopyWindowInfo(CG.kCGWindowListOptionOnScreenOnly, CG.kCGNullWindowID)
        owned = [w for w in windows if int(w.get(CG.kCGWindowOwnerPID, 0)) == pid
                 and int(w.get(CG.kCGWindowLayer, -1)) == 0
                 and float(w.get(CG.kCGWindowBounds, {}).get("Width", 0)) > 200
                 and float(w.get(CG.kCGWindowBounds, {}).get("Height", 0)) > 200]
        if not owned:
            return unavailable("未找到目标聊天窗口", bundle)
        window_id = int(owned[0][CG.kCGWindowNumber])
        if getattr(target, "window_handle", 0) and int(target.window_handle) != window_id:
            return unavailable("目标窗口已变化", bundle)
        from ..browser_bridge.provenance import snapshot as browser_snapshot, same_page
        website_before = browser_snapshot(bundle)
        content = _await(lambda cb: SC.SCShareableContent.getShareableContentExcludingDesktopWindows_onScreenWindowsOnly_completionHandler_(True, True, cb))
        window = next((w for w in content.windows() if int(w.windowID()) == window_id), None)
        if window is None or int(window.owningApplication().processID()) != pid:
            return unavailable("目标窗口已变化", bundle)
        manager = compatibility.screenshot_manager(SC)
        if manager is None:
            image = compatibility.legacy_window_image(CG, window_id)
        else:
            frame = window.frame()
            scale = min(2.0, 2400 / max(frame.size.width, frame.size.height))
            config = SC.SCStreamConfiguration.alloc().init()
            config.setWidth_(round(frame.size.width * scale)); config.setHeight_(round(frame.size.height * scale))
            config.setShowsCursor_(False); config.setCapturesAudio_(False)
            if hasattr(config, "setIgnoreShadowsSingleWindow_"):
                config.setIgnoreShadowsSingleWindow_(True)
            content_filter = SC.SCContentFilter.alloc().initWithDesktopIndependentWindow_(window)
            image = _await(lambda cb: manager.captureImageWithFilter_configuration_completionHandler_(content_filter, config, cb))
        if image is None or NSWorkspace.sharedWorkspace().frontmostApplication().processIdentifier() != pid:
            return unavailable("截图期间目标应用已变化", bundle)
        captured = time.perf_counter()
        captured_at = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
        website_after = browser_snapshot(bundle) if website_before else None
        # A failed OCR or PNG encoder must not erase the other piece of evidence.
        try:
            result = recognize_image(image, bundle)
        except Exception as exc:
            result = unavailable(str(exc), bundle)
        result = ScreenContext(result, evidence=getattr(result, "evidence", {}))
        sharing_state = owned[0].get(CG.kCGWindowSharingState)
        if result.get("error_code") == "transparent_window_capture" and sharing_state == 0:
            result["reason"] += "（系统报告该窗口不可共享）"
        result.update(application_name=application_name,
                      capture_id=uuid.uuid4().hex, window_id=window_id, process_id=pid,
                      capture_ms=round((captured - started) * 1000), ocr_ms=round((time.perf_counter() - captured) * 1000))
        result.evidence.update(captured_at=captured_at, image_width=int(CG.CGImageGetWidth(image)),
                               image_height=int(CG.CGImageGetHeight(image)), capture_scope="target_window",
                               capture_method="screen_capture_kit_window" if manager else "legacy_window",
                               window_sharing_state=sharing_state)
        if same_page(website_before, website_after):
            result.evidence["website_evidence"] = dict(application=bundle, website=website_before["website"],
                source="browser_extension", captured_at=captured_at, page=website_before["page"])
        try:
            result.evidence["screenshot_png"] = _encode_png(image)
        except Exception as exc:
            result.evidence["screenshot_error"] = str(exc)
        return result


def _encode_png(image):
    import Quartz as CG
    from Foundation import NSMutableData
    data = NSMutableData.data()
    destination = CG.CGImageDestinationCreateWithData(data, "public.png", 1, None)
    if destination is None:
        raise RuntimeError("无法编码上文截图")
    CG.CGImageDestinationAddImage(destination, image, None)
    if not CG.CGImageDestinationFinalize(destination):
        raise RuntimeError("无法编码上文截图")
    return bytes(data)


class ScreenContextProvider:
    def __init__(self, capture=None):
        self._capture = capture or capture_context
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="VoiceScreenContext")

    def prefetch(self, target):
        requested_at = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
        def work():
            try:
                context = self._capture(target)
            except Exception as exc:
                context = unavailable(str(exc), str(getattr(target, "process_name", "")))
            context["requested_at"] = requested_at
            return context
        return self._pool.submit(work)

    def close(self):
        self._pool.shutdown(wait=False, cancel_futures=True)
