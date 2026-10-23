from types import SimpleNamespace

import pytest

from proximic_ring.desktop.window_selector import WindowSelectorSession, grid_move
from test_text_focus import Node
from proximic_ring.desktop.text_focus import FocusError


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
        self.pointer = (100, 100)
        self.session = WindowSelectorSession(self, clock=lambda: self.now)
    def front(self): return dict(self.stamp)
    def on_screen(self): return self.rows
    def screens(self): return self.displays
    def pointer_position(self): return self.pointer
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


@pytest.mark.parametrize("origin", ["closed", "minimized", "no_app", "own_overlay"])
def test_windowless_foreground_still_lists_other_visible_apps(origin):
    d = Desktop()
    if origin == "no_app":
        d.stamp = {"pid": 0, "window": 0}
    else:
        d.rows = [r for r in d.rows if r["pid"] != 10]
        d.application_windows[10] = []
        d.app_nodes[10].attrs["AXFocusedWindow"] = None
        if origin == "minimized": d.a.attrs["AXMinimized"] = True
        if origin == "own_overlay":
            d.rows.insert(0, {"pid": 10, "number": 99, "frame": (0, 0, 1440, 900)})
    result = d.session.handle("selector_list", host_pid=10 if origin == "own_overlay" else 0)
    assert result["status"] == "ready"
    assert any(c["pid"] == 20 for c in result["cards"])
    assert d.session.last_diagnostic["screen_source"] == "pointer"
    assert d.session.last_diagnostic["reason"] == "ready"
    target = next(c for c in result["cards"] if c["pid"] == 20)
    assert d.session.handle("selector_activate", token=result["token"], target=target["id"])["status"] == "activated"
    assert d.activated == [(20, d.c)]


def test_windowless_foreground_chooses_pointer_display_in_global_coordinates():
    d = Desktop()
    d.stamp = {"pid": 0, "window": 0}
    d.pointer = (-900, -50)
    d.c.frame = (-1200, -100, 600, 400)
    d.rows[1]["frame"] = d.c.frame
    result = d.listing()
    assert result["screen"] == 2
    assert [c["title"] for c in result["cards"]] == ["Gamma"]


def test_missing_pointer_falls_back_to_visible_window_and_never_to_old_snapshot():
    d = Desktop()
    d.listing()
    d.stamp = {"pid": 0, "window": 0}
    d.pointer = None
    d.rows = [r for r in d.rows if r["pid"] == 20]
    result = d.listing()
    assert [c["title"] for c in result["cards"]] == ["Gamma"]
    assert d.session.last_diagnostic["screen_source"] == "visible_window"
    d.rows.clear()
    assert d.listing()["status"] == "empty"
    assert d.session.last_diagnostic["reason"] == "no_visible_windows"
    assert not d.session.targets and not d.session.token


def test_unavailable_displays_are_not_reported_as_empty_desktop():
    d = Desktop()
    d.displays.clear()
    assert d.listing()["status"] == "unavailable"
    assert d.session.last_diagnostic["reason"] == "screens_unavailable"


def test_transient_origin_read_failure_does_not_disable_global_selection():
    d = Desktop()
    def failed_front(): raise RuntimeError("private window name")
    d.front = failed_front
    result = d.listing()
    assert result["status"] == "ready" and len(result["cards"]) == 3
    assert d.session.last_diagnostic["screen_source"] == "pointer"
    assert d.session.last_diagnostic["origin_error"]["error_type"] == "RuntimeError"
    assert "private" not in str(d.session.last_diagnostic)


def test_stale_origin_geometry_keeps_current_visible_window_listing():
    d = Desktop()
    stale = Node("AXWindow")
    d.app_nodes[10].attrs["AXFocusedWindow"] = stale
    original = d.rect
    def rect(node):
        if node is stale: raise RuntimeError("stale focus")
        return original(node)
    d.rect = rect
    assert len(d.listing()["cards"]) == 3
    assert d.session.last_diagnostic["screen_source"] == "foreground_window"


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


@pytest.mark.parametrize('operation', ['AXUIElementGetAttributeValueCount', 'AXUIElementCopyAttributeValues'])
def test_window_read_failure_keeps_ax_code_and_attribute(operation):
    from types import SimpleNamespace
    from proximic_ring.desktop.text_focus import FocusError
    from proximic_ring.desktop.window_selector import MacWindowAX
    from proximic_ring.diagnostics.scene_diagnostics import exception_details, safe_data
    ax = MacWindowAX.__new__(MacWindowAX)
    ax.application = lambda pid: object()
    ax.ax = SimpleNamespace(kAXErrorAttributeUnsupported=-25205, kAXErrorNoValue=-25212,
        AXUIElementGetAttributeValueCount=lambda *args: (-25204, 0) if operation.endswith('Count') else (0, 1),
        AXUIElementCopyAttributeValues=lambda *args: (-25204, None))
    with pytest.raises(FocusError) as raised:
        ax.windows(42)
    details = safe_data(exception_details(raised.value))
    assert raised.value.status == 'unavailable'
    assert details['ax_error'] == -25204
    assert details['ax_operation'] == operation and details['ax_attribute'] == 'AXWindows'


@pytest.mark.parametrize('host', [False, True])
def test_transient_read_timeout_recovers_for_every_app_before_one_activation(host):
    d = Desktop()
    listing = (d.session.handle('selector_list', host_pid=10, host_window=dict(number=1, title='Alpha'))
               if host else d.listing())
    target = next(c for c in listing['cards'] if c['pid'] == 10)
    original, attempts = d.windows, []
    def windows(pid):
        attempts.append(pid)
        if len(attempts) <= 2:
            raise FocusError('unavailable', ax_error=-25204,
                             ax_operation='AXUIElementGetAttributeValueCount', ax_attribute='AXWindows')
        return original(pid)
    d.windows = windows
    result = d.session.handle('selector_activate', token=listing['token'], target=target['id'])
    assert result['status'] == 'activated' and len(d.activated) == 1 and attempts == [10, 10, 10]
    assert d.session.last_diagnostic['ax_read_timeouts'] == 2
    assert d.session.last_diagnostic['ax_error'] == -25204


@pytest.mark.parametrize('attribute', ['AXRole', 'AXSubrole', 'AXMinimized', 'AXHidden'])
def test_transient_window_property_failure_recovers_without_changing_selected_window(attribute):
    d = Desktop()
    listing = d.listing()
    original, failures = d.attr, []
    def attr(node, name):
        if node is d.b and name == attribute and not failures:
            failures.append(name)
            raise FocusError('unavailable', ax_error=-25204,
                             ax_operation='AXUIElementCopyAttributeValue', ax_attribute=name)
        return original(node, name)
    d.attr = attr
    assert d.activate_card(listing)['status'] == 'activated'
    assert d.activated == [(10, d.b)] and failures == [attribute]
    assert d.session.last_diagnostic['ax_attribute'] == attribute


@pytest.mark.parametrize('attribute', ['AXRole', 'AXSubrole', 'AXMinimized', 'AXHidden', 'rect'])
@pytest.mark.parametrize('vanished_target', [False, True])
def test_vanished_sharing_window_does_not_abort_but_vanished_selected_window_stays_stale(attribute, vanished_target):
    d = Desktop()
    listing = d.listing()
    helper = Node('AXWindow', frame=d.b.frame)
    vanished = d.b if vanished_target else helper
    if not vanished_target: d.application_windows[10].append(helper)
    original_attr, original_rect = d.attr, d.rect
    def fail(node, name):
        if node is vanished and name == attribute:
            raise FocusError('unavailable', ax_error=-25202,
                             ax_operation='AXUIElementCopyAttributeValue', ax_attribute=name)
    def attr(node, name):
        fail(node, name)
        if node is helper:
            return {'AXRole': 'AXWindow', 'AXSubrole': 'AXStandardWindow'}.get(name)
        return original_attr(node, name)
    def rect(node):
        fail(node, 'rect')
        return original_rect(node)
    d.attr, d.rect = attr, rect
    outcome = d.activate_card(listing)
    assert outcome['status'] == ('stale' if vanished_target else 'activated')
    assert d.activated == ([] if vanished_target else [(10, d.b)])
    assert d.session.last_diagnostic['vanished_windows'] == 1


@pytest.mark.parametrize('actually_removed', [True, False])
def test_illegal_argument_is_only_ignored_when_a_fresh_window_list_proves_the_object_disappeared(actually_removed):
    d = Desktop()
    listing = d.listing()
    helper = Node('AXWindow')
    d.application_windows[10].append(helper)
    original_attr, original_windows = d.attr, d.windows
    reads = []
    def windows(pid):
        reads.append(pid)
        return list(original_windows(pid))
    def attr(node, name):
        if node is helper:
            if actually_removed: d.application_windows[10].remove(helper)
            raise FocusError('unavailable', ax_error=-25201,
                             ax_operation='AXUIElementCopyAttributeValue', ax_attribute=name)
        return original_attr(node, name)
    d.windows, d.attr = windows, attr
    if actually_removed:
        assert d.activate_card(listing)['status'] == 'activated'
        assert d.activated == [(10, d.b)]
        assert d.session.last_diagnostic['vanished_ax_error'] == -25201
    else:
        with pytest.raises(FocusError): d.activate_card(listing)
        assert not d.activated
    assert reads == [10, 10]


@pytest.mark.parametrize('change', ['closed', 'minimized', 'other_space', 'app_closed'])
def test_recovery_refreshes_visibility_instead_of_activating_stale_target(change):
    d = Desktop()
    listing = d.listing()
    original, attempts = d.windows, []
    def windows(pid):
        attempts.append(pid)
        if len(attempts) == 1:
            if change == 'closed': d.application_windows[10].remove(d.b)
            elif change == 'minimized': d.b.attrs['AXMinimized'] = True
            elif change == 'other_space': d.rows = [r for r in d.rows if r['number'] != 2]
            elif change == 'app_closed': del d.applications[10]
            raise FocusError('unavailable', ax_error=-25204)
        return original(pid)
    d.windows = windows
    assert d.activate_card(listing)['status'] == 'stale' and not d.activated


@pytest.mark.parametrize('error,attempts', [(-25204, 4), (-25202, 1), (-25211, 1)])
def test_persistent_read_failure_is_bounded_and_never_activates(error, attempts):
    d = Desktop()
    listing = d.listing()
    reads = []
    def windows(pid):
        reads.append(pid)
        raise FocusError('unavailable', ax_error=error)
    d.windows = windows
    with pytest.raises(FocusError): d.activate_card(listing)
    assert len(reads) == attempts and not d.activated
    assert d.activate_card(listing)['reason'] == 'session_invalid'


def test_read_retry_respects_remaining_budget_and_does_not_retry_writes():
    from proximic_ring.desktop.window_selector import retry_window_read
    reads, pauses = [], []
    def read():
        reads.append(True)
        raise FocusError('unavailable', ax_error=-25204)
    with pytest.raises(FocusError):
        retry_window_read(read, deadline=.07, clock=lambda: 0, pause=pauses.append)
    assert reads == [True] and not pauses
    d = Desktop()
    listing = d.listing()
    def activate(app, window):
        d.activated.append((app.pid, window))
        raise FocusError('unavailable', ax_error=-25204)
    d.activate = activate
    with pytest.raises(FocusError): d.activate_card(listing)
    assert len(d.activated) == 1


def test_activation_focus_verification_recovers_without_repeating_mutations():
    from proximic_ring.desktop.window_selector import MacWindowAX
    ax = MacWindowAX.__new__(MacWindowAX)
    ax.application = lambda pid: 'app'
    ax.settable = lambda node, name: True
    ax.front = lambda: dict(pid=42)
    writes, reads = [], []
    ax.ax = SimpleNamespace(
        AXUIElementSetAttributeValue=lambda *args: writes.append(args) or 0,
        AXUIElementPerformAction=lambda *args: writes.append(args) or 0)
    def attr(node, name):
        reads.append(name)
        if len(reads) == 1:
            raise FocusError('unavailable', ax_error=-25204,
                             ax_operation='AXUIElementCopyAttributeValue', ax_attribute=name)
        return 'window'
    ax.attr = attr
    app = SimpleNamespace(processIdentifier=lambda: 42,
                          activateWithOptions_=lambda value: writes.append(('activate', value)) or True)
    assert ax.activate(app, 'window')
    assert reads == ['AXFocusedWindow', 'AXFocusedWindow']
    assert writes == [('app', 'AXFocusedWindow', 'window'), ('window', 'AXRaise'), ('activate', 0)]
    assert ax.activation_diagnostic['verification_timeouts'] == 1


@pytest.mark.parametrize('failed_step', ['AXFocusedWindow', 'AXFrontmost', 'AXRaise', 'activate'])
@pytest.mark.parametrize('reached_target', [True, False])
def test_activation_reply_failure_checks_exact_outcome_without_replaying_writes(failed_step, reached_target):
    from proximic_ring.desktop.window_selector import MacWindowAX
    ax = MacWindowAX.__new__(MacWindowAX)
    ax.application = lambda pid: 'app'
    ax.settable = lambda node, name: True
    writes, failed = [], []
    ax.front = lambda: dict(pid=42 if failed else 7)
    ax.attr = lambda node, name: 'target' if reached_target else 'another-window'
    def write(attribute):
        writes.append(attribute)
        if attribute == failed_step:
            failed.append(True)
            return -25204
        return 0
    ax.ax = SimpleNamespace(
        AXUIElementSetAttributeValue=lambda node, attribute, value: write(attribute),
        AXUIElementPerformAction=lambda node, action: write(action))
    app = SimpleNamespace(processIdentifier=lambda: 42,
                          activateWithOptions_=lambda value: write('activate') == 0)
    assert ax.activate(app, 'target') is reached_target
    steps = ['AXFocusedWindow', 'AXFrontmost', 'AXRaise', 'activate']
    assert writes == steps[:steps.index(failed_step) + 1]
    if failed_step == 'activate':
        assert ax.activation_diagnostic['activation_accepted'] is False
    else:
        assert ax.activation_diagnostic['mutation_error'] == -25204
        assert ax.activation_diagnostic['mutation_attribute'] == failed_step
