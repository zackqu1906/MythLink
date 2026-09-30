"""Browser presets, page identity and website-specific routing boundaries."""
from dataclasses import replace
import json

import pytest
from PySide6.QtCore import QCoreApplication

from proximic_ring.scene_recognition.browser import detect_browser as browser_media_context
from proximic_ring.scene_recognition.websites import website_domain, matches_website
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
    ("fake_domain", "", "nontext"), ("loading", "", "unknown"),
    ("editable_unknown", "video", "unknown"), ("timeout", "", "unknown"),
    ("outside_button", "video", "unknown"), ("player_button", "video", "nontext"),
])
def test_bilibili_requires_real_player_current_page_and_nontext_focus(change, scene, context):
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
    if change == "fake_domain": web.attrs["AXURL"] = "https://bilibili.com.evil.test/video"
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
    assert not browser_media_context(window, web, read).scene
    button = Node("AXButton", video)
    assert browser_media_context(window, button, read).scene == "video"
    window, web, wrapper, video = page()
    Node("AXGroup", wrapper, AXSubrole="AXVideo", AXEnabled=True)
    assert not browser_media_context(window, web, read).scene
    window, web, wrapper, video = page()
    wrapper.attrs["AXRole"] = "AXWebArea"  # Do not use an embedded frame's player as the parent page's.
    assert not browser_media_context(window, web, read).scene


@pytest.mark.parametrize("value,expected", [("https://www.bilibili.com/video/BV123?token=secret", BILI),
    ("BILIBILI.COM", BILI), ("player.example.com", "player.example.com"), ("https://example.com:443/a", "example.com")])
def test_domains_drop_path_query_and_credentials(value, expected):
    assert website_domain(value) == expected


@pytest.mark.parametrize("value", ["", "bad host", "file:///tmp/a", "https://user:password@example.com", "localhost", "*.example.com"])
def test_invalid_domains_are_rejected(value):
    with pytest.raises(ValueError): website_domain(value)


def test_domain_boundaries_are_exact_or_subdomain_not_substrings():
    assert matches_website("www.bilibili.com", BILI)
    assert not matches_website("bilibili.com.evil.test", BILI)
    assert not matches_website("fakebilibili.com", BILI)


def test_website_bindings_persist_and_remain_independent_of_generic_scene(route, monkeypatch):
    c, service, _, _, _, _, _ = route
    catalog = configure(service, monkeypatch, SAFARI)
    catalog.selectScene("video")
    assert catalog.browserVideoScope and catalog.websites == [dict(value="", label="通用视频")]
    assert {item["shortcut"] for item in catalog.actions if item.get("preset")} == {"Space", "Left", "Right", "Up", "Down"}
    assert catalog.setBinding(SAFARI, "tap", "web-video:play")
    assert catalog.addWebsite("https://www.bilibili.com/video/BV123?secret=omitted")
    assert catalog.selectedWebsite == BILI and catalog.bindingCount(SAFARI) == 10
    assert catalog.bindings[SAFARI]["swipe-left"]["shortcut"] == "Left"
    assert not catalog.voice_overridden(SAFARI)
    assert catalog.setCustomBinding(SAFARI, "tap", "自定义播放", "Alt+Space")
    assert catalog.setBinding(SAFARI, "swipe-left", "")
    assert catalog.addWebsite(BILI)  # Does not reinstall deleted defaults.
    assert "swipe-left" not in catalog.bindings[SAFARI]
    assert catalog.for_scene(SAFARI, "video", "www.bilibili.com")["scene:tap"].shortcut == "Alt+Space"
    assert catalog.for_scene(SAFARI, "video", "other.test")["scene:tap"].shortcut == "Space"
    assert "secret" not in c._settings.value(SETTINGS_KEY)
    restored = ApplicationMappingController(service)
    try:
        assert restored.for_scene(SAFARI, "video", BILI) == catalog.for_scene(SAFARI, "video", BILI)
    finally:
        restored.close()
    assert catalog.addWebsite("player.bilibili.com")
    assert catalog.for_scene(SAFARI, "video", "player.bilibili.com") == {}  # Longest domain wins, even if empty.
    assert catalog.removeWebsite(SAFARI, "player.bilibili.com")
    assert catalog.for_scene(SAFARI, "video", "player.bilibili.com")["scene:tap"].shortcut == "Alt+Space"
    catalog.clearApplicationBindings(SAFARI)
    catalog.refreshMenu()
    assert catalog.bindingCount(SAFARI) == 0
    assert catalog.for_scene(SAFARI, "video", BILI) == {}
    assert catalog.regularBindings[SAFARI] == {}


@pytest.mark.parametrize("change", ["page", "tab", "player", "input", "domain"])
def test_switching_tabs_pages_players_or_inputs_rejects_old_target_before_keydown(desktop, change):
    backend, state, _ = desktop
    original = ShortcutTarget(SAFARI, 42, SAFARI, "window", "web", "AXWebArea", scene="video", scene_checked=True,
                              input_context="nontext", website=BILI, page_key="page1", web_area="tab1", player="video1")
    changes = {"page": dict(page_key="page2"), "tab": dict(web_area="tab2"), "player": dict(player="video2"),
               "input": dict(input_context="text"), "domain": dict(website="other.test")}
    state.target = replace(original, **changes[change])
    with pytest.raises(RuntimeError): backend.post(original, "Space")
    assert state.sent == []


def test_scene_dispatch_selects_current_website_not_editor_selection_and_empty_never_falls_back(presentation, monkeypatch):
    c, service, _, backend, _, sent, _ = presentation
    catalog = configure(service, monkeypatch, SAFARI)
    catalog.selectScene("video")
    catalog.setBinding(SAFARI, "swipe-left", "web-video:forward")
    catalog.addWebsite(BILI)
    catalog.setBinding(SAFARI, "swipe-left", "")
    catalog.selectWebsite("")
    backend.target = replace(backend.target, bundle=SAFARI, scene="video", website=BILI, page_key="one", input_context="nontext")
    assert not request(c, "swipe-left") and sent == []
    assert not request(c, "tap") and sent == [(SAFARI, "Space")]
    assert not request(c, "swipe-right", deliver=False)
    backend.target = replace(backend.target, website="example.test", page_key="two")
    QCoreApplication.processEvents()
    assert sent == [(SAFARI, "Space")]
    assert not request(c, "swipe-left") and sent[-1] == (SAFARI, "Right")
    backend.target = replace(backend.target, input_context="text")
    request(c, "tap")
    assert len(sent) == 2
