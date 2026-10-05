from types import SimpleNamespace

import pytest

from proximic_ring.window_selector import WindowSelectorSession, grid_move
from test_text_focus import Node


class Desktop:
    def __init__(self):
        self.now = 0
        self.a = Node("AXWindow", frame=(10, 30, 800, 600), attrs={"AXTitle": "Alpha", "AXSubrole": "AXStandardWindow"})
        self.b = Node("AXWindow", frame=(40, 70, 700, 500), attrs={"AXTitle": "Beta", "AXSubrole": "AXStandardWindow"})
        self.c = Node("AXWindow", frame=(150, 100, 600, 400), attrs={"AXTitle": "Gamma", "AXSubrole": "AXStandardWindow"})
        self.app_nodes = {10: Node("AXApplication", attrs={"AXFocusedWindow": self.a}),
                          20: Node("AXApplication", attrs={"AXFocusedWindow": self.c})}
        self.application_windows = {10: [self.a, self.b], 20: [self.c]}
        self.applications = {pid: SimpleNamespace(pid=pid) for pid in self.app_nodes}
        self.rows = [{"pid": 10, "number": 1, "frame": self.a.frame},
                     {"pid": 20, "number": 3, "frame": self.c.frame},
                     {"pid": 10, "number": 2, "frame": self.b.frame}]
        self.displays = [{"id": 1, "frame": (0, 0, 1440, 900)},
                         {"id": 2, "frame": (-1440, -200, 1440, 900)}]
        self.stamp = {"pid": 10, "window": 1}
        self.activated = []
        self.session = WindowSelectorSession(self, clock=lambda: self.now)
    def front(self): return dict(self.stamp)
    def on_screen(self): return self.rows
    def screens(self): return self.displays
    def apps(self): return self.applications
    def application(self, pid): return self.app_nodes[pid]
    def windows(self, pid): return self.application_windows[pid]
    def attr(self, node, name):
        assert name not in {"AXValue", "AXSelectedText", "AXChildren"}
        return node.role if name == "AXRole" else node.attrs.get(name)
    def rect(self, node): return node.frame
    def app_info(self, app): return {"app": "App " + str(app.pid), "bundle": "test.app." + str(app.pid)}
    def activate(self, app, node):
        self.activated.append((app.pid, node))
        return True
    def listing(self): return self.session.handle("selector_list", expected=self.stamp)
    def activate_card(self, listing, index=2):
        return self.session.handle("selector_activate", token=listing["token"], target=listing["cards"][index]["id"])


@pytest.mark.parametrize("index,direction,expected", [(0,"left",0), (2,"right",2), (0,"up",0),
    (1,"down",4), (5,"down",5), (7,"right",7), (7,"down",7), (6,"up",3), (4,"left",3)])
def test_grid_respects_rows_and_short_last_row(index, direction, expected):
    assert grid_move(index, 8, 3, direction) == expected


def test_cards_order_initial_selection_and_exact_same_app_window_activation():
    d = Desktop()
    result = d.listing()
    assert result["status"] == "ready" and result["selected"] == 0
    assert [c["title"] for c in result["cards"]] == ["Alpha", "Gamma", "Beta"]
    assert d.activate_card(result) == {"status": "activated"}
    assert d.activated == [(10, d.b)]
    assert d.activate_card(result) == {"status": "stale", "reason": "session_invalid"}
    assert len(d.activated) == 1


@pytest.mark.parametrize("kind", ["minimized", "hidden", "dialog", "other_space", "other_screen", "tiny"])
def test_non_candidates_do_not_become_cards(kind):
    d = Desktop()
    if kind == "minimized": d.b.attrs["AXMinimized"] = True
    elif kind == "hidden": d.b.attrs["AXHidden"] = True
    elif kind == "dialog": d.b.attrs["AXSubrole"] = "AXDialog"
    elif kind == "other_space": d.rows.pop()
    elif kind == "other_screen":
        d.b.frame = (-1200, -100, 700, 500)
        d.rows[-1]["frame"] = d.b.frame
    else:
        d.b.frame = (10, 20, 100, 80)
        d.rows[-1]["frame"] = d.b.frame
    assert [c["title"] for c in d.listing()["cards"]] == ["Alpha", "Gamma"]


def test_identical_geometry_is_not_guessed_across_spaces_but_same_space_duplicates_work():
    d = Desktop()
    d.b.frame = d.a.frame
    d.rows.pop()  # The second same-sized window is in another Space.
    assert [c["title"] for c in d.listing()["cards"]] == ["Gamma"]
    d.rows.append({"pid": 10, "number": 2, "frame": d.a.frame})
    result = d.listing()
    assert len(result["cards"]) == 3
    index = next(i for i, c in enumerate(result["cards"]) if c["title"] == "Beta")
    assert d.activate_card(result, index)["status"] == "activated"
    assert d.activated == [(10, d.b)]


@pytest.mark.parametrize("change", ["closed", "minimized", "other_space", "app_closed", "cancelled", "new_session"])
def test_stale_target_or_released_session_cannot_activate(change):
    d = Desktop()
    result = d.listing()
    if change == "closed": d.application_windows[10].remove(d.b)
    elif change == "minimized": d.b.attrs["AXMinimized"] = True
    elif change == "other_space": d.rows = [r for r in d.rows if r["number"] != 2]
    elif change == "app_closed": del d.applications[10]
    elif change == "cancelled": d.session.handle("selector_cancel", token=result["token"])
    else: d.listing()
    assert d.activate_card(result)["status"] == "stale"
    assert not d.activated


@pytest.mark.parametrize("change", ["front", "focus", "missing_focus", "origin_closed", "screen", "elapsed"])
def test_origin_changes_do_not_prevent_explicit_target_activation(change):
    d = Desktop()
    result = d.listing()
    if change == "front": d.stamp = {"pid": 20, "window": 3}
    elif change == "focus": d.app_nodes[10].attrs["AXFocusedWindow"] = d.b
    elif change == "missing_focus": d.app_nodes[10].attrs["AXFocusedWindow"] = None
    elif change == "origin_closed": d.rows = [r for r in d.rows if r["number"] != 1]
    elif change == "screen": d.displays[0] = {"id": 1, "frame": (0, 0, 1920, 1080)}
    else: d.now = 30  # User activity owns expiry; no hidden 15-second lease.
    assert d.activate_card(result)["status"] == "activated"
    assert d.activated == [(10, d.b)]


def test_delayed_cancel_cannot_release_a_new_session():
    d = Desktop()
    old = d.listing()
    new = d.listing()
    d.session.handle("selector_cancel", token=old["token"])
    assert d.activate_card(new)["status"] == "activated"


def test_initial_request_stamp_is_not_an_environment_guard():
    d = Desktop()
    result = d.session.handle("selector_list", expected={"pid": 999, "window": 999})
    assert result["status"] == "ready"


def test_partial_app_failure_does_not_discard_working_windows_or_log_content():
    d = Desktop()
    windows = d.windows
    def read(pid):
        if pid == 20: raise RuntimeError("unresponsive app")
        return windows(pid)
    d.windows = read
    result = d.listing()
    assert result["partial"] and len(result["cards"]) == 2


def test_only_registered_host_main_window_is_included_and_can_activate():
    import json
    d = Desktop()
    # A host overlay may even report AXStandardWindow and identical geometry.
    d.b.frame = d.a.frame
    d.rows[-1]["frame"] = d.a.frame
    result = d.session.handle("selector_list", host_pid=10,
                              host_window={"number": 1, "title": "Alpha"})
    assert [c["title"] for c in result["cards"]] == ["Alpha", "Gamma"]
    assert result["cards"][0]["app"] == "Mythlink"
    assert result["cards"][0]["number"] == 1
    json.dumps(result)
    assert d.activate_card(result, 0)["status"] == "activated"
    assert d.activated == [(10, d.a)]


@pytest.mark.parametrize("kind", ["unregistered", "closed", "minimized", "other_space"])
def test_unavailable_host_main_does_not_substitute_a_helper(kind):
    d = Desktop()
    main = {"number": 1, "title": "Alpha"}
    if kind == "unregistered": main = {}
    elif kind == "closed": d.application_windows[10].remove(d.a)
    elif kind == "minimized": d.a.attrs["AXMinimized"] = True
    else: d.rows = [r for r in d.rows if r["number"] != 1]
    result = d.session.handle("selector_list", host_pid=10, host_window=main)
    assert [c["title"] for c in result["cards"]] == ["Gamma"]


def test_foreground_without_ax_focus_does_not_disable_the_global_selector():
    d = Desktop()
    d.app_nodes[10].attrs["AXFocusedWindow"] = None
    result = d.listing()
    assert result["status"] == "ready"
    assert d.activate_card(result, 1)["status"] == "activated"
    assert d.activated == [(20, d.c)]


def test_card_limit_bounds_ipc_size_and_unresponsive_app_budget_is_partial():
    d = Desktop()
    for i in range(130):
        node = Node("AXWindow", frame=(10 + i, 40, 810, 610),
                    attrs={"AXTitle": "测试" * 200, "AXSubrole": "AXStandardWindow"})
        d.application_windows[20].append(node)
        d.rows.append({"pid": 20, "number": 100 + i, "frame": node.frame})
    result = d.listing()
    assert result["partial"] and len(result["cards"]) == 128
    assert all(len(c["title"]) <= 160 for c in result["cards"])


def test_preview_ids_are_exact_or_omitted_not_guessed_for_equal_window_geometry():
    d = Desktop()
    d.b.frame = d.a.frame
    d.rows[-1]["frame"] = d.a.frame
    result = d.listing()
    assert all(not c["number"] for c in result["cards"] if c["pid"] == 10)
    d.rows[0]["title"] = "Alpha"
    d.rows[-1]["title"] = "Beta"
    result = d.listing()
    assert {c["title"]: c["number"] for c in result["cards"]} == {"Alpha":1,"Beta":2,"Gamma":3}
