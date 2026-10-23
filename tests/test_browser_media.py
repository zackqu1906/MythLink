"""Automatic browser scenes, shared navigation and fresh page identity."""
from dataclasses import replace
import json

import pytest
from PySide6.QtCore import QCoreApplication

from proximic_ring.scenes.recognition.browser import detect_browser as browser_media_context
from proximic_ring.desktop.app_shortcuts import ShortcutTarget, LocalMacAppShortcuts
from proximic_ring.ui.desktop.application_mapping_controller import ApplicationMappingController, SETTINGS_KEY
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
    assert key not in {"AXValue", "AXSelectedText"}
    assert key != 'AXTitle' or node.attrs['AXRole'] in {'AXButton', 'AXCheckBox', 'AXSlider', 'AXValueIndicator'}
    return node.attrs.get(key)


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
