"""Automatic browser scenes, shared navigation and fresh page identity."""
from dataclasses import replace
import json

import pytest
from PySide6.QtCore import QCoreApplication

from proximic_ring.scene_recognition.browser import detect_browser as browser_media_context
from proximic_ring.app_shortcuts import ShortcutTarget, LocalMacAppShortcuts
from proximic_ring.ui.application_mapping_controller import ApplicationMappingController, SETTINGS_KEY
from test_app_gestures import route
from test_application_menus import configure
from test_gesture_scenes import presentation
from test_ring_gestures import request
from test_app_shortcuts import desktop

SAFARI = "com.apple.Safari"
BILI = "bilibili.com"


class Node:
    def __init__(self, role, parent=None, **attrs):
        self.attrs = dict(AXRole=role, AXParent=parent, **attrs)
        if parent is not None:
            parent.attrs.setdefault("AXChildren", []).append(self)


def page():
    window = Node("AXWindow")
    web = Node("AXWebArea", window, AXURL="https://www.bilibili.com/video/BVtest/?p=1", AXValueSettable=False)
    wrapper = Node("AXGroup", web, AXDOMClassList=["bpx-player-primary-area"])
    video = Node("AXGroup", wrapper, AXSubrole="AXVideo", AXEnabled=True)
    Node("AXButton", wrapper, AXDescription="Pause", AXEnabled=True)
    Node("AXSlider", wrapper, AXDescription="Playback progress", AXEnabled=True)
    return window, web, wrapper, video


def read(node, key):
    assert key not in {"AXValue", "AXSelectedText", "AXTitle"}
    return node.attrs.get(key)


@pytest.mark.parametrize("change,scene,context", [
    ("ready", "video", "nontext"), ("paused", "video", "nontext"),
    ("comment", "video", "text"), ("editable_web", "video", "text"),
    ("address", "", "unknown"), ("missing_focus", "", "unknown"),
    ("foreign_window", "", "unknown"), ("modal", "", "unknown"),
    ("no_player", "", "nontext"), ("hidden", "", "nontext"),
    ("disabled", "", "nontext"), ("thumbnail", "", "nontext"),
    ("another_domain", "video", "nontext"), ("loading", "", "unknown"),
    ("editable_unknown", "video", "unknown"), ("timeout", "", "unknown"),
    ("outside_button", "video", "unknown"), ("player_button", "video", "nontext"),
])
def test_any_site_requires_real_player_current_page_and_nontext_focus(change, scene, context):
    window, web, wrapper, video = page()
    focus = web
    if change == "paused": video.attrs["AXDescription"] = "Paused video"
    if change == "comment": focus = Node("AXTextArea", web)
    if change == "editable_web": web.attrs["AXEditable"] = True
    if change == "address": focus = Node("AXTextField", window)
    if change == "missing_focus": focus = None
    if change == "foreign_window": web.attrs["AXParent"] = Node("AXWindow")
    if change == "modal": window.attrs["AXModal"] = True
    if change == "no_player": wrapper.attrs["AXChildren"] = []
    if change == "hidden": wrapper.attrs["AXHidden"] = True
    if change == "disabled": video.attrs["AXEnabled"] = False
    if change == "thumbnail": video.attrs["AXSubrole"] = "AXImage"
    if change == "another_domain": web.attrs["AXURL"] = "https://bilibili.com.evil.test/video"
    if change == "loading": web.attrs["AXElementBusy"] = True
    if change == "editable_unknown": web.attrs.pop("AXValueSettable")
    if change == "outside_button": focus = Node("AXButton", web)
    if change == "player_button": focus = Node("AXButton", wrapper)
    result = browser_media_context(window, focus, read, budget=0 if change == "timeout" else .18)
    assert (result.scene, result.input_context) == (scene, context)
    assert "BVtest" not in repr(result) and "?p=1" not in repr(result)


def test_generic_video_requires_focus_in_player_and_rejects_iframe_and_ambiguous_players():
    window, web, wrapper, video = page()
    web.attrs["AXURL"] = "https://example.test/watch"
    wrapper.attrs["AXChildren"] = [video]  # Focus required when controls are not exposed.
    assert not browser_media_context(window, web, read).scene
    button = Node("AXButton", video)
    assert browser_media_context(window, button, read).scene == "video"
    window, web, wrapper, video = page()
    Node("AXGroup", wrapper, AXSubrole="AXVideo", AXEnabled=True)
    assert not browser_media_context(window, web, read).scene
    window, web, wrapper, video = page()
    wrapper.attrs["AXRole"] = "AXWebArea"  # Do not use an embedded frame's player as the parent page's.
    assert not browser_media_context(window, web, read).scene


@pytest.mark.parametrize("change", ["page", "tab", "player", "input"])
def test_switching_tabs_pages_players_or_inputs_rejects_old_target_before_keydown(desktop, change):
    backend, state, _ = desktop
    original = ShortcutTarget(SAFARI, 42, SAFARI, "window", "web", "AXWebArea", scene="video", scene_checked=True,
                              input_context="nontext", page_key="page1", web_area="tab1", player="video1")
    changes = {"page": dict(page_key="page2"), "tab": dict(web_area="tab2"), "player": dict(player="video2"),
               "input": dict(input_context="text")}
    state.target = replace(original, **changes[change])
    with pytest.raises(RuntimeError): backend.post(original, "Space")
    assert state.sent == []


def test_scene_dispatch_uses_current_page_not_editor_and_cancels_stale_gestures(presentation, monkeypatch):
    c, service, _, backend, _, sent, _ = presentation
    catalog = configure(service, monkeypatch, SAFARI)
    catalog.selectScene("video")
    assert catalog.setBinding(SAFARI, "swipe-left", "web-video:forward")
    catalog.selectScene("regular")
    backend.target = replace(backend.target, bundle=SAFARI, scene="video", page_key="one", input_context="nontext")
    assert not request(c, "swipe-left") and sent == [(SAFARI, "Right")]
    assert not request(c, "tap") and sent[-1] == (SAFARI, "Space")
    assert not request(c, "swipe-right", deliver=False)
    backend.target = replace(backend.target, page_key="two")
    QCoreApplication.processEvents()
    assert len(sent) == 2
    backend.target = replace(backend.target, input_context="text")
    request(c, "tap")
    assert len(sent) == 2
