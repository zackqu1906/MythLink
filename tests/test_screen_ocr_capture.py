"""A transparent native screenshot is capture failure, not empty OCR."""
import sys
from types import SimpleNamespace

import pytest

from proximic_ring.text_processing.screen_context import recognize_image, _encode_png
from proximic_ring.text_processing.context_preparation import ContextPreparationProvider
from proximic_ring.voice.modification_dataset import ModificationDatasetCollector
from test_routed_edit import processor_returning, settings


def native_image(alpha):
    if sys.platform != "darwin":
        pytest.skip("native CoreGraphics bitmap")
    import Quartz as CG
    pixels = bytearray([0, 0, 0, alpha] * 400 * 300)
    canvas = CG.CGBitmapContextCreate(pixels, 400, 300, 8, 400 * 4,
        CG.CGColorSpaceCreateDeviceRGB(), CG.kCGImageAlphaPremultipliedLast | CG.kCGBitmapByteOrder32Big)
    return CG.CGBitmapContextCreateImage(canvas)


@pytest.fixture
def vision_calls(monkeypatch):
    calls = []
    request = SimpleNamespace(setRecognitionLevel_=lambda *_: None,
        setRecognitionLanguages_=lambda *_: None, setUsesLanguageCorrection_=lambda *_: None,
        results=lambda: [])
    handler = SimpleNamespace(performRequests_error_=lambda *args: calls.append(args) or (True, None))
    monkeypatch.setitem(sys.modules, "Vision", SimpleNamespace(VNRequestTextRecognitionLevelAccurate=1,
        VNRecognizeTextRequest=SimpleNamespace(alloc=lambda: SimpleNamespace(init=lambda: request)),
        VNImageRequestHandler=SimpleNamespace(alloc=lambda: SimpleNamespace(initWithCGImage_options_=lambda *args: handler))))
    return calls


def test_fully_transparent_capture_is_rejected_before_ocr(vision_calls):
    raw = recognize_image(native_image(0), "com.tencent.xinWeChat")
    assert raw["status"] == "unavailable"
    assert raw["error_code"] == "transparent_window_capture"
    assert "全透明" in raw["reason"] and raw.evidence["ocr_lines"] == []
    assert not vision_calls


def test_opaque_black_window_still_runs_ocr(vision_calls):
    raw = recognize_image(native_image(255), "com.tencent.xinWeChat")
    assert raw["status"] == "empty" and vision_calls
    assert "error_code" not in raw


def test_capture_failure_survives_preparation_and_history(tmp_path, vision_calls):
    image = native_image(0)
    raw = recognize_image(image, "com.tencent.xinWeChat")
    raw.evidence["screenshot_png"] = _encode_png(image)
    processor, calls = processor_returning("unused")
    provider = ContextPreparationProvider(processor)
    try:
        prepared = provider.prepare(raw, settings())
    finally:
        provider.close()
    assert "全透明" in prepared["reason"] and not calls
    store = ModificationDatasetCollector(tmp_path, "capture-test")
    identity = store.record_screen_capture(7, raw, routing="manual_routed_edit")
    store.record_context_preparation(identity, prepared, enabled=True)
    saved = store._interaction_data(identity)
    assert saved["screen_evidence"]["screenshot_file"]
    sections = {row["key"]: row for row in store.load_details(identity)["sections"]}
    assert sections["screenshot"]["errorText"] == raw["reason"]
    assert "全透明" in sections["context_preparation"]["items"][0]["text"]
