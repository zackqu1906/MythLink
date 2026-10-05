import sys
import weakref

import pytest

from proximic_ring.ui.menu_bar import MacMenuBar, connection_title


@pytest.mark.parametrize("connected,available,value,charging,expected", [
    (False, True, 71, False, "未连接"),
    (True, False, 71, False, "已连接"),
    (True, True, -1, False, "已连接"),
    (True, True, 101, False, "已连接"),
    (True, True, 0, False, "已连接 · 0%"),
    (True, True, 71, False, "已连接 · 71%"),
    (True, True, 71, True, "已连接 · 71% 充电"),
])
def test_connection_title(connected, available, value, charging, expected):
    assert connection_title(connected, available, value, charging) == expected


def test_changed_values_only_and_disconnect_clears_battery():
    class Button:
        images = []
        def setImage_(self, image): self.images.append(image)
        def setAccessibilityLabel_(self, label): self.label = label
        def setToolTip_(self, text): self.tooltip = text

    button = Button()
    class Item:
        def button(self): return button

    bar = MacMenuBar.__new__(MacMenuBar)
    bar._item = Item()
    bar._last_state = None
    bar._last_show_info = None
    bar._tooltip = "Mythlink"
    bar._make_status_image = lambda *state: state
    bar.setConnectionStatus(True, True, 71, False)
    bar.setConnectionStatus(True, True, 71, False)
    bar.setConnectionStatus(True, True, 70, False)
    bar.setConnectionStatus(False, True, 70, False)
    assert button.images == [(True, 71, False, True), (True, 70, False, True), (False, None, False, True)]
    assert "未连接" in button.label
    assert "70%" not in button.tooltip


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS native callback")
def test_native_callback_does_not_dispatch_disabled_or_released_actions():
    from Foundation import NSObject
    from proximic_ring.ui.menu_bar import _menu_target_class

    class Action:
        enabled = True
        calls = 0
        def isEnabled(self): return self.enabled
        def trigger(self): self.calls += 1

    class Sender:
        def tag(self): return 0

    action = Action()
    bar = MacMenuBar.__new__(MacMenuBar)
    bar._actions = [action]
    target = _menu_target_class().alloc().init()
    target.owner = weakref.ref(bar)
    assert issubclass(type(target), NSObject)
    assert _menu_target_class() is _menu_target_class()
    target.invoke_(Sender())
    action.enabled = False
    target.invoke_(Sender())
    bar._actions = []
    target.invoke_(Sender())
    del bar
    target.invoke_(Sender())
    assert action.calls == 1


def test_hide_removes_only_owned_item_once():
    removed = []
    class Bar:
        def removeStatusItem_(self, item): removed.append(item)

    bar = MacMenuBar.__new__(MacMenuBar)
    item = object()
    bar._item = item
    bar._bar = Bar()
    bar.hide()
    bar.hide()
    bar.setConnectionStatus(True, True, 80, False)
    assert removed == [item]
