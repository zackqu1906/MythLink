"""Shared slideshow evidence across applications, versions and displays."""
from collections import Counter
from dataclasses import replace
import sys
from types import SimpleNamespace

import pytest

from proximic_ring.app_shortcuts import LocalMacAppShortcuts
from proximic_ring.gesture_scenes import POWERPOINT, PRESENTATION
from scene_test_helpers import presentation_context
from test_gesture_scenes import metadata, window_tree, wps_window_tree
from test_app_gestures import route
from test_application_menus import configure

WPS = "com.kingsoft.wpsoffice.mac"
PRESENTERS = [WPS, POWERPOINT, "com.apple.iWork.Keynote", "org.libreoffice.script",
              "org.openoffice.script", "asc.onlyoffice.ONLYOFFICE", "test.independent.slides"]


@pytest.mark.parametrize("title", ["PowerPoint Slide Show - Quarterly Review.pptx",
    "Microsoft PowerPoint - 幻灯片放映 — Review.pptx",
    "PowerPoint 幻燈片放映 - Review.pptx", "PowerPoint Presenter View - Review.pptx",
    "WPS 演示 - 幻灯片放映 - Review.dps", "幻灯片放映 - Review.pptx", "Slide Show - Review.pptx"])
def test_generated_show_title_accepts_document_extension(title):
    window, focus = window_tree(title)
    assert presentation_context(WPS if title.startswith("WPS") else POWERPOINT,
                                window, focus, metadata) == (PRESENTATION, "nontext")


@pytest.mark.parametrize("bundle", PRESENTERS)
@pytest.mark.parametrize("variant", ["standard_window", "optional_attributes", "display_rounding", "nested_canvas",
                                     "borderless_second_display", "direct_window_reference", "document_title",
                                     "plain_title", "missing_title"])
def test_complete_slide_surface_across_presenters_and_display_layouts(bundle, variant):
    window, focus = wps_window_tree()
    frames = ()
    if variant == "standard_window": window["AXSubrole"] = "AXStandardWindow"
    if variant == "document_title": window["AXTitle"] = "Review.pptx"
    if variant == "plain_title": window["AXTitle"] = "Presentation1"
    if variant == "missing_title": window.pop("AXTitle")
    if variant == "optional_attributes":
        for key in ("AXModal", "AXMinimized"): window.pop(key)
        for key in ("AXFocused", "AXEnabled"): focus.pop(key)
    if variant == "display_rounding":
        focus.update(AXPosition=(.5, 1.), AXSize=(1919., 1078.))
    if variant == "nested_canvas":
        parent = dict(AXRole="AXGroup", AXParent=window, AXChildren=[focus])
        window["AXChildren"] = [parent]
        focus["AXParent"] = parent
    if variant == "borderless_second_display":
        window.pop("AXFullScreen")
        window["AXPosition"] = focus["AXPosition"] = (-1920., -120.)
        frames = ((0., 0., 1440., 900.), (-1920., -120., 1920., 1080.))
    if variant == "direct_window_reference":
        focus.pop("AXParent"); focus["AXWindow"] = window
    assert presentation_context(bundle, window, focus, metadata, screen_frames=frames,
                                profile="generic") == (PRESENTATION, "nontext")


@pytest.mark.parametrize("change", ["editor_ribbon", "input", "prompt", "sheet", "offscreen", "small_canvas", "focus_elsewhere"])
def test_borderless_geometry_never_qualifies_an_editor_or_unrelated_focus(change):
    window, focus = wps_window_tree()
    window.pop("AXFullScreen")
    frames = ((0., 0., 1920., 1080.),)
    if change == "editor_ribbon": window["AXChildren"].append(dict(AXRole="AXToolbar"))
    if change == "input": focus["AXEditable"] = True
    if change == "prompt": window["AXChildren"].append(dict(AXRole="AXButton", AXTitle="OK"))
    if change == "sheet": window["AXSheets"] = [dict(AXRole="AXSheet")]
    if change == "offscreen": frames = ((1920., 0., 1920., 1080.),)
    if change == "small_canvas": focus["AXSize"] = (800., 600.)
    if change == "focus_elsewhere": focus["AXParent"] = dict(AXRole="AXWindow", AXTitle="Other")
    assert presentation_context(WPS, window, focus, metadata, screen_frames=frames) == ("", "unknown")


@pytest.mark.parametrize("extension", ["pdf", "docx", "xlsx", "png", "mp4"])
@pytest.mark.parametrize("bundle", PRESENTERS)
def test_fullscreen_other_content_does_not_use_slide_bindings(bundle, extension):
    window, focus = wps_window_tree()
    window["AXDocument"] = "file:///Review." + extension
    assert presentation_context(bundle, window, focus, metadata, profile="generic") == ("", "unknown")


@pytest.mark.parametrize("evidence", ["title", "controls", "canvas"])
def test_generic_presenter_needs_no_shortcut_profile_for_shared_detection(evidence):
    from proximic_ring.scenes.recognition.presentation import detect_presentation
    from proximic_ring.gesture_scenes import scene_actions
    name = "Example Slides [Plus]"
    window, focus = window_tree(name + " - Slide Show - Review.odp")
    if evidence == "controls":
        window["AXTitle"] = "Review.odp"
        window["AXChildren"] = [dict(AXRole="AXButton", AXTitle="End Show", AXEnabled=True)]
    if evidence == "canvas": window, focus = wps_window_tree()
    result = detect_presentation(window, focus, metadata, application_names=[name])
    assert (result.scene, result.input_context) == (PRESENTATION, "nontext")
    assert presentation_context("test.independent.slides", window, focus, metadata,
                                profile="generic", application_name=name) == (PRESENTATION, "nontext")
    assert not scene_actions("test.independent.slides", PRESENTATION, profile="generic")
    assert presentation_context("test.unrelated.application", window, focus, metadata) == ("", "unknown")


def native_backend(monkeypatch, window, focus, *, bundle=POWERPOINT):
    import proximic_ring.mac_workspace as workspace
    monkeypatch.setattr(sys, "platform", "darwin")
    front = SimpleNamespace(bundleIdentifier=lambda: bundle, processIdentifier=lambda: 42,
                            localizedName=lambda: "Office")
    monkeypatch.setattr(workspace, "frontmost_application", lambda: front)
    root, reads = dict(AXFocusedWindow=window, AXFocusedUIElement=focus), Counter()
    def read(node, key, _):
        reads[id(node), key] += 1
        return 0, metadata(node, key)
    monkeypatch.setitem(sys.modules, "ApplicationServices", SimpleNamespace(
        AXUIElementCreateApplication=lambda pid: root, AXUIElementSetMessagingTimeout=lambda *args: None,
        AXUIElementCopyAttributeValue=read, AXValueGetValue=lambda value, kind, _: (True, value),
        kAXValueCGPointType=1, kAXValueCGSizeType=2))
    return LocalMacAppShortcuts(), root, reads


@pytest.mark.parametrize("bundle", PRESENTERS)
@pytest.mark.parametrize("evidence", ["display_geometry", "installed_name"])
def test_native_shared_evidence_is_available_to_every_presenter(monkeypatch, tmp_path, bundle, evidence):
    from test_application_catalog import application
    import proximic_ring.mac_workspace as workspace
    window, focus = wps_window_tree()
    window.pop("AXFullScreen")
    window["AXSubrole"] = "AXStandardWindow"
    if evidence == "installed_name": window, focus = window_tree("Office - Slide Show - Review.odp")
    backend, _, _ = native_backend(monkeypatch, window, focus, bundle=bundle)
    path = application(tmp_path / 'Slides.app', bundle, CFBundleDocumentTypes=[{
        'CFBundleTypeRole': 'Editor', 'CFBundleTypeExtensions': ['odp']}])
    front = workspace.frontmost_application()
    front.bundleURL = lambda: SimpleNamespace(path=lambda: str(path))
    screen = SimpleNamespace(deviceDescription=lambda: {"NSScreenNumber": 1})
    bounds = SimpleNamespace(origin=SimpleNamespace(x=0., y=0.), size=SimpleNamespace(width=1920., height=1080.))
    monkeypatch.setitem(sys.modules, "AppKit", SimpleNamespace(NSScreen=SimpleNamespace(screens=lambda: [screen])))
    monkeypatch.setitem(sys.modules, "Quartz", SimpleNamespace(CGDisplayBounds=lambda _: bounds))
    target = backend.capture(menu_action=True, scene=True)
    assert target.scene == PRESENTATION and target.input_context == "nontext" and not target.blocked
    assert backend.same_target(target)


def test_scene_uses_actual_focus_window_instead_of_stale_editor_and_revalidates(monkeypatch):
    editor, _ = window_tree("Review.pptx")
    show, focus = window_tree("PowerPoint Slide Show - Review.pptx")
    focus["AXWindow"] = show
    backend, root, _ = native_backend(monkeypatch, editor, focus)
    target = backend.capture(menu_action=True, scene=True)
    assert target.window is show and target.scene == PRESENTATION and not target.blocked
    assert backend.same_target(target)
    root["AXFocusedUIElement"] = dict(AXRole="AXTextArea", AXWindow=editor, AXParent=editor)
    assert not backend.same_target(target)


@pytest.mark.parametrize("kind", ["search_field", "combo_box", "description"])
def test_explicit_office_shortcut_is_not_subject_to_chat_composer_guard(monkeypatch, kind):
    window, focus = window_tree("Review.pptx")
    if kind == "search_field": focus["AXRole"] = "AXSearchField"
    if kind == "combo_box": focus["AXRole"] = "AXComboBox"
    if kind == "description": focus["AXDescription"] = "搜索工具"
    backend, _, _ = native_backend(monkeypatch, window, focus)
    assert not backend.capture(menu_action=True).blocked
    window["AXSheets"] = [dict(AXRole="AXSheet")]
    assert backend.capture(menu_action=True).blocked


def test_capture_caches_only_within_snapshot_and_discards_mid_read_focus_change(monkeypatch):
    window, focus = wps_window_tree()
    backend, root, reads = native_backend(monkeypatch, window, focus, bundle=WPS)
    first = backend.capture(menu_action=True, scene=True)
    assert first.scene == PRESENTATION
    # The snapshot cache is reused except for the two identities deliberately
    # checked again at the boundary to reject mid-read document/window changes.
    revalidated = {(id(window), "AXDocument"), (id(focus), "AXWindow")}
    assert all(count == (2 if (node, key) in revalidated else 1)
               for (node, key), count in reads.items() if node != id(root))
    focus["AXRole"] = "AXTextField"
    assert not backend.same_target(first)  # New snapshot must reread all metadata.
    focus["AXRole"] = "AXGroup"
    ax = sys.modules["ApplicationServices"]
    original = ax.AXUIElementCopyAttributeValue
    def switch(node, key, unused):
        result = original(node, key, unused)
        if node is focus and key == "AXRole":
            root["AXFocusedUIElement"] = dict(AXRole="AXTextField", AXParent=window)
        return result
    ax.AXUIElementCopyAttributeValue = switch
    assert backend.capture(menu_action=True, scene=True) is None


def test_slower_ax_responses_still_identify_wps_without_reusing_a_previous_snapshot(monkeypatch):
    import proximic_ring.scenes.recognition.presentation as scenes
    window, focus = wps_window_tree()
    backend, _, _ = native_backend(monkeypatch, window, focus, bundle=WPS)
    ax, elapsed = sys.modules["ApplicationServices"], [0.]
    original = ax.AXUIElementCopyAttributeValue
    def slow_read(*args):
        elapsed[0] += .004  # Cross-process AX calls on a slower machine.
        return original(*args)
    monkeypatch.setattr(scenes.time, "monotonic", lambda: elapsed[0])
    ax.AXUIElementCopyAttributeValue = slow_read
    target = backend.capture(menu_action=True, scene=True)
    assert target.scene == PRESENTATION and target.input_context == "nontext"
    assert elapsed[0] > .08  # The former WPS scene deadline was only 80 ms.


def test_office_mapping_block_reports_window_not_chat_input(route, monkeypatch):
    _, service, _, backend, sent, messages, emit = route
    catalog = configure(service, monkeypatch, POWERPOINT)
    assert catalog.setCustomBinding(POWERPOINT, "snap", "从当前页放映", "Cmd+Return")
    backend.target = replace(backend.target, bundle=POWERPOINT, profile=POWERPOINT, blocked=True)
    monkeypatch.setattr(backend, "capture", lambda **options: replace(backend.target, menu_action=True,
                                                                    scene_checked=options.get("scene", False)))
    emit("snap")
    assert not sent and not messages
    assert "聊天" not in service.notice and "弹窗" in service.notice
