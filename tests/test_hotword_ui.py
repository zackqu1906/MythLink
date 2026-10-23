import pytest

from test_inline_ui import inline_ui
from test_ui_service_settings import item, click, screenshot


def open_library(root, size):
    from PySide6.QtCore import QPointF
    from PySide6.QtTest import QTest
    root.resize(*size)
    root.setProperty("currentPage", 1)
    root.show()
    QTest.qWait(80)
    scroll = item(root, "mainPageScroll").property("contentItem")
    before = scroll.property("contentY")
    click(root, "voiceHotwordLibraryButton")
    QTest.qWait(350)
    library = item(root, "personalHotwordSettings")
    assert library.property("visible")
    assert scroll.property("contentY") == pytest.approx(before, abs=1)
    # Opening preserves the viewport. Scroll explicitly before operating on tags.
    top = library.mapToItem(scroll, QPointF()).y() + scroll.property("contentY")
    scroll.setProperty("contentY", min(top, max(0, scroll.property("contentHeight") - scroll.height())))
    QTest.qWait(50)


def tag(root, entry):
    from test_ui_shell import visual_child
    result = visual_child(item(root, "hotwordList"), "hotwordTag_" + entry["id"])
    assert result is not None
    return result


def point(target, fraction=0.5):
    from PySide6.QtCore import QPointF
    return target.mapToScene(QPointF(target.width() * fraction, target.height() / 2)).toPoint()


def drag(root, start, end, *, release=True):
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest
    QTest.mousePress(root, Qt.LeftButton, Qt.NoModifier, start)
    # Real press-and-move, with no hold or sorting-mode button.
    for step in range(1, 9):
        QTest.mouseMove(root, QPoint(round(start.x() + (end.x() - start.x()) * step / 8),
                                    round(start.y() + (end.y() - start.y()) * step / 8)), 12)
    assert item(root, "personalHotwordSettings").property("dragging")
    if release:
        QTest.mouseRelease(root, Qt.LeftButton, Qt.NoModifier, end)
        QTest.qWait(200)


@pytest.mark.parametrize("size", [(1440, 940), (1200, 780), (940, 700)])
def test_compact_library_hover_actions_add_search_and_edit(inline_ui, tmp_path, size):
    from PySide6.QtCore import QObject, QPoint, QPointF, Qt
    from PySide6.QtTest import QTest
    from test_ui_shell import visual_child
    c, bridge, _, root, _ = inline_ui
    c.asrBackend = "volcengine"
    c._connected = True
    c.connectedChanged.emit()
    before = list(bridge.messages), c.inlineInput._utterance_id, c.inlineInput._view.copy()
    for word in ["Kubernetes", "Mythlink", "张玮", "OpenTelemetry", "ProxiMic", "林梓涵", "云原生", "PostgreSQL", "星河计划", "TensorFlow", "字节跳动", "北京大学"]:
        c.hotwords.add(word)
    open_library(root, size)
    field = item(root, "hotwordAddField")
    field.setProperty("text", "语音交互")
    click(root, "hotwordAddButton")
    assert field.property("text") == "" and c.hotwords.store.snapshot()[0] == "语音交互"
    words = item(root, "hotwordList")
    search = item(root, "hotwordSearchField")
    search.setProperty("text", "kube")
    QTest.qWait(50)
    assert words.property("count") == 1
    assert root.findChild(QObject, "hotwordSortButton") is None
    search.setProperty("text", "")
    QTest.qWait(180)
    short = next(e for e in c.hotwords.entries if e["word"] == "张玮")
    long = next(e for e in c.hotwords.entries if e["word"] == "OpenTelemetry")
    assert tag(root, short).width() < tag(root, long).width()
    label = visual_child(tag(root, long), "hotwordLabel_" + long["id"])
    assert label.width() >= label.property("implicitWidth")
    first = c.hotwords.entries[0]
    assert tag(root, first).height() == 40 and tag(root, first).width() < 140
    QTest.mouseMove(root, point(tag(root, first)), 20)
    QTest.qWait(150)
    actions = item(root, "hotwordHoverActions")
    assert actions.property("visible")
    face = tag(root, first)
    face_origin = face.mapToScene(QPointF())
    actions_origin = actions.mapToScene(QPointF())
    assert abs(actions_origin.x() + actions.width() - face_origin.x() - face.width()) <= 3
    assert abs(actions_origin.y() + actions.height() / 2 - face_origin.y()) <= 1
    for name, symbol in [("hotwordEditButton", "edit"), ("hotwordDeleteButton", "minus")]:
        control = item(root, name)
        icon = item(root, name + "Icon")
        circle = item(root, name + "Face")
        assert circle.width() == circle.height() == 14
        assert control.width() >= circle.width() and control.height() > circle.height()
        assert icon.width() >= 10 and icon.height() >= 10 and icon.isVisible()
        assert icon.property("symbol") == symbol
    screenshot(root, tmp_path, f"hotword-hover-{size[0]}.png")
    edit = item(root, "hotwordEditButton")
    # Move gradually into the controls, including their shared gap.
    start, end = point(face), point(edit)
    for step in range(1, 7):
        QTest.mouseMove(root, QPoint(round(start.x() + (end.x() - start.x()) * step / 6),
                                    round(start.y() + (end.y() - start.y()) * step / 6)), 15)
    gap = actions.mapToScene(QPointF(actions.width() / 2, actions.height() / 2)).toPoint()
    QTest.mouseMove(root, gap, 20)
    QTest.qWait(120)
    assert actions.property("visible")
    QTest.mouseMove(root, end, 30)
    QTest.qWait(180)
    assert actions.property("visible") and edit.property("hovered")
    QTest.mouseClick(root, Qt.LeftButton, Qt.NoModifier, point(edit))
    QTest.qWait(130)
    assert item(root, "hotwordRenameDialog").property("visible")
    item(root, "hotwordRenameField").setProperty("text", "语音交互设计")
    item(root, "hotwordEnabledToggle").setProperty("checked", False)
    click(root, "hotwordRenameSaveButton")
    QTest.qWait(200)
    assert c.hotwords.entries[0]["word"] == "语音交互设计"
    assert not c.hotwords.entries[0]["enabled"]
    QTest.mouseMove(root, point(tag(root, first)), 20)
    QTest.qWait(130)
    delete = item(root, "hotwordDeleteButton")
    QTest.mouseMove(root, point(delete), 30)
    QTest.qWait(180)
    assert actions.property("visible") and delete.property("hovered")
    QTest.mouseClick(root, Qt.LeftButton, Qt.NoModifier, point(delete))
    QTest.qWait(150)
    assert first["id"] not in [e["id"] for e in c.hotwords.entries]
    assert not item(root, "personalHotwordSettings").property("dragging")
    screenshot(root, tmp_path, f"hotword-library-{size[0]}.png")
    assert before == (list(bridge.messages), c.inlineInput._utterance_id, c.inlineInput._view.copy())
    click(root, "hotwordCollapseButton")
    QTest.qWait(250)
    assert not item(root, "mainPageScroll").property("hotwordsExpanded")


@pytest.mark.parametrize("size", [(1440, 940), (940, 700)])
@pytest.mark.parametrize("mixed_width", [False, True])
def test_drag_to_arbitrary_middle_across_rows_and_escape(inline_ui, tmp_path, size, mixed_width):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from proximic_ring.voice.hotwords import HotwordStore
    c, _, _, root, _ = inline_ui
    varied = ["张玮", "OpenTelemetry", "小林", "云原生", "Kubernetes", "张梓涵", "SQL", "星河计划", "字节跳动", "TensorFlow",
              "PostgreSQL", "北京大学", "语音交互", "ProxiMic", "Mythlink", "豆包", "Anthropic", "设计系统", "王珂", "Ringo"]
    for i in range(20):
        c.hotwords.add(varied[i] if mixed_width else f"专业词语{i:02}")
    open_library(root, size)
    original = c.hotwords.entries
    start = point(tag(root, original[0]))
    second_row = next(i for i, e in enumerate(original) if point(tag(root, e)).y() > start.y() + 20)
    destination = second_row + 1
    end = point(tag(root, original[destination]), .9)
    assert end.y() < root.height()
    drag(root, start, end, release=False)
    # Unequal tags rewrap as the placeholder moves. Follow the visible target
    # after that transition, just as a user positions the pointer beside a tag.
    QTest.qWait(180)
    end = point(tag(root, original[destination]), .9)
    QTest.mouseMove(root, end, 20)
    panel = item(root, "personalHotwordSettings")
    assert panel.property("dragIndex") == destination
    QTest.qWait(180)
    screenshot(root, tmp_path, f"hotword-drag-{size[0]}.png")
    QTest.mouseRelease(root, Qt.LeftButton, Qt.NoModifier, end)
    QTest.qWait(200)
    expected = [e["id"] for e in original]
    moved = expected.pop(0)
    expected.insert(destination, moved)
    assert [e["id"] for e in c.hotwords.entries] == expected
    assert [e["id"] for e in HotwordStore(c.hotwords.store.path).entries()] == expected
    start = point(tag(root, original[0]))
    target = c.hotwords.entries[2]
    end = point(tag(root, target), .1)
    drag(root, start, end, release=False)
    QTest.qWait(180)
    end = point(tag(root, target), .1)
    QTest.mouseMove(root, end, 20)
    QTest.mouseRelease(root, Qt.LeftButton, Qt.NoModifier, end)
    QTest.qWait(200)
    expected.insert(2, expected.pop(destination))
    assert [e["id"] for e in c.hotwords.entries] == expected
    start = point(tag(root, c.hotwords.entries[0]))
    end = point(tag(root, c.hotwords.entries[4]), .9)
    drag(root, start, end, release=False)
    QTest.keyClick(root, Qt.Key_Escape)
    QTest.qWait(180)
    assert point(tag(root, c.hotwords.entries[0])).y() == start.y()
    QTest.mouseRelease(root, Qt.LeftButton, Qt.NoModifier, end)
    QTest.qWait(100)
    assert not panel.property("dragging")
    assert [e["id"] for e in c.hotwords.entries] == expected


def test_drag_to_end_preserves_concurrent_learning_and_auto_scrolls(inline_ui):
    from PySide6.QtCore import QPoint, QPointF, Qt
    from PySide6.QtTest import QTest
    c, _, _, root, _ = inline_ui
    for i in range(60):
        c.hotwords.add(f"专业词语{i:02}")
    open_library(root, (940, 700))
    panel = item(root, "personalHotwordSettings")
    words = item(root, "hotwordList")
    surface = item(root, "hotwordDragSurface")
    origin = surface.mapToScene(QPointF())
    original = c.hotwords.entries[0]
    start = point(tag(root, original))
    end = QPoint(round(origin.x() + surface.width() - 8), round(origin.y() + surface.height() - 8))
    drag(root, start, end, release=False)
    c.hotwords.store.learn("concurrent", ["新学到的词"])
    c.hotwords.changed.emit()
    for _ in range(80):
        QTest.qWait(30)
        if words.property("contentY") >= words.property("contentHeight") - words.height() - 1:
            break
    assert words.property("contentY") > 0
    QTest.mouseRelease(root, Qt.LeftButton, Qt.NoModifier, end)
    QTest.qWait(200)
    assert c.hotwords.entries[0]["word"] == "新学到的词"
    assert c.hotwords.entries[-1]["id"] == original["id"]
    assert not panel.property("dragging")


def test_search_blocks_reorder_and_keyboard_remains_available(inline_ui):
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest
    c, _, _, root, _ = inline_ui
    for word in ["张玮", "Kubernetes", "OpenTelemetry"]:
        c.hotwords.add(word)
    open_library(root, (940, 700))
    first = c.hotwords.entries[0]
    center = point(tag(root, first))
    QTest.mouseClick(root, Qt.LeftButton, Qt.NoModifier, center)
    QTest.keyClick(root, Qt.Key_Right, Qt.AltModifier)
    QTest.qWait(180)
    assert c.hotwords.entries[1]["id"] == first["id"]
    item(root, "hotwordSearchField").setProperty("text", "Telemetry")
    QTest.qWait(180)
    expected = c.hotwords.entries
    center = point(tag(root, first))
    QTest.mousePress(root, Qt.LeftButton, Qt.NoModifier, center)
    QTest.mouseMove(root, center + QPoint(80, 0), 30)
    assert not item(root, "personalHotwordSettings").property("dragging")
    QTest.mouseRelease(root, Qt.LeftButton, Qt.NoModifier, center + QPoint(80, 0))
    assert c.hotwords.entries == expected


@pytest.mark.parametrize("action", ["edit", "delete"])
@pytest.mark.parametrize("path", ["across_top", "immediate_edge"])
def test_hover_controls_remain_reachable_across_the_tag_top(inline_ui, action, path):
    from PySide6.QtCore import QPoint, QPointF, Qt
    from PySide6.QtTest import QTest
    c, _, _, root, _ = inline_ui
    for word in ["张玮", "OpenTelemetry", "Kubernetes"]:
        c.hotwords.add(word)
    open_library(root, (940, 700))
    entry = c.hotwords.entries[0]
    face = tag(root, entry)
    start = point(face, .2)
    QTest.mouseMove(root, start, 20)
    if path == "across_top":
        QTest.qWait(150)
    actions = item(root, "hotwordHoverActions")
    button = item(root, "hotwordEditButton" if action == "edit" else "hotwordDeleteButton")
    end = point(button)
    if path == "across_top":
        # A natural bent path: move above the label, then across to its controls.
        # Pause longer than the old 90 ms exit timer while still over this tag.
        corner = QPoint(start.x(), end.y() - 3)
        QTest.mouseMove(root, corner, 40)
        QTest.qWait(250)
        assert actions.property("visible")
    else:
        # Reach the transparent click margin before the reveal animation finishes.
        end = button.mapToScene(QPointF(button.width() / 2, 2)).toPoint()
        circle = item(root, button.objectName() + "Face")
        assert end.y() < circle.mapToScene(QPointF()).y()
    QTest.mouseMove(root, end, 1)
    QTest.qWait(160)
    assert button.property("hovered")
    QTest.mouseClick(root, Qt.LeftButton, Qt.NoModifier, end)
    QTest.qWait(120)
    assert not item(root, "personalHotwordSettings").property("dragging")
    if action == "edit":
        dialog = item(root, "hotwordRenameDialog")
        assert dialog.property("visible") and dialog.property("entryId") == entry["id"]
    else:
        assert entry["id"] not in [e["id"] for e in c.hotwords.entries]


@pytest.mark.parametrize("action", ["edit", "delete"])
def test_action_press_keeps_its_word_when_library_updates(inline_ui, action):
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest
    c, _, _, root, _ = inline_ui
    c.hotwords.add("Kubernetes")
    c.hotwords.add("张玮")
    open_library(root, (940, 700))
    entry = c.hotwords.entries[0]
    QTest.mouseMove(root, point(tag(root, entry)), 20)
    QTest.qWait(150)
    actions = item(root, "hotwordHoverActions")
    # Leaving the whole region dismisses the controls; returning reveals them.
    QTest.mouseMove(root, point(item(root, "hotwordSearchField")), 20)
    QTest.qWait(240)
    assert not actions.property("visible")
    QTest.mouseMove(root, point(tag(root, entry)), 20)
    QTest.qWait(150)
    button = item(root, "hotwordEditButton" if action == "edit" else "hotwordDeleteButton")
    end = point(button)
    QTest.mouseMove(root, end, 20)
    QTest.mousePress(root, Qt.LeftButton, Qt.NoModifier, end)
    assert button.property("pressed")
    c.hotwords.add("后台新词")
    QTest.qWait(200)
    assert actions.property("visible") and button.property("pressed")
    QTest.mouseRelease(root, Qt.LeftButton, Qt.NoModifier, end + QPoint(1, 0))
    QTest.qWait(200)
    if action == "edit":
        dialog = item(root, "hotwordRenameDialog")
        assert dialog.property("visible") and dialog.property("entryId") == entry["id"]
    else:
        assert entry["id"] not in [e["id"] for e in c.hotwords.entries]
    assert c.hotwords.entries[0]["word"] == "后台新词"
    assert item(root, "hotwordList").property("count") == len(c.hotwords.entries)
