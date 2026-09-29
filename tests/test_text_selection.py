import pytest

from proximic_ring.text_selection import spatial_target
from test_text_focus import Desktop, Node


RECTS = [(40,96,520,46), (40,178,250,46), (310,178,250,46),
         (40,260,520,110), (40,406,250,46), (310,406,250,46)]


def spatial_desktop():
    d = Desktop()
    d.window.children = []
    nodes = [Node("AXTextField", editable=True, frame=rect) for rect in RECTS]
    d.window.add(*nodes)
    return d, nodes


def select(d, action="enter", token="", direction="", apply=True):
    result = d.session.handle("focus_selection", action=action, selection=token,
                              direction=direction, expected=d.front())
    return d.apply(result) if apply and "plan" in result else result


def test_html_spatial_cost_overlap_rules_and_edges_do_not_wrap():
    assert spatial_target(RECTS, 0, "down") == 3  # 196 vs 82 + 2.2*135, per the HTML's cost.
    assert spatial_target(RECTS, 1, "right") == 2
    assert spatial_target(RECTS, 2, "left") == 1
    assert spatial_target(RECTS, 1, "down") == 4
    assert spatial_target(RECTS, 3, "down") == 4
    assert spatial_target(RECTS, 0, "right") is None
    assert spatial_target(RECTS, 4, "down") is None


def test_enter_highlights_current_field_and_navigation_focuses_ordinary_fields():
    d, nodes = spatial_desktop()
    d.app.attrs["AXFocusedUIElement"] = nodes[1]
    entered = select(d)
    assert entered["index"] == 2 and entered["count"] == 6
    assert entered["fields"][1]["rect"] == list(RECTS[1])
    assert not d.writes
    moved = select(d, "move", entered["selection"], "right")
    assert moved["index"] == 3 and d.writes == [nodes[2]]
    boundary = select(d, "move", entered["selection"], "right", apply=False)
    assert boundary["bounce"] and boundary["index"] == 3
    d.session.handle("focus_selection", action="cancel", selection=entered["selection"])
    assert d.app.attrs["AXFocusedUIElement"] is nodes[2]


def browser_desktop():
    d = Desktop()
    d.app.attrs["bundle"] = "com.apple.Safari"
    d.window.children = []
    address = Node("AXTextField", editable=True, frame=(40,30,520,35), attrs={"AXIdentifier": "address-field"})
    field = Node("AXTextArea", editable=True, frame=(40,160,520,140))
    d.window.add(Node("AXToolbar").add(address), Node("AXWebArea").add(field))
    return d, address, field


def test_address_selection_and_timeout_do_not_focus_but_tap_confirmation_does():
    d, address, ordinary = browser_desktop()
    result = select(d)
    token = result["selection"]
    assert result["deferred"] and "plan" not in result and not d.writes
    moved = select(d, "move", token, "down")
    assert not moved["deferred"] and d.writes == [ordinary]
    moved = select(d, "move", token, "up")
    assert moved["deferred"] and d.writes == [ordinary]
    assert d.app.attrs["AXFocusedUIElement"] is ordinary
    confirmed = select(d, "confirm", token)
    assert confirmed["status"] == "focused" and d.writes == [ordinary, address]


def test_autofocus_skips_address_and_page_toolbar_text_fields_are_ordinary():
    d, address, ordinary = browser_desktop()
    assert d.apply(d.plan("restore"))["index"] == 2
    assert d.writes == [ordinary]
    d.window.children = [d.window.children[0]]
    d.app.attrs["AXFocusedUIElement"] = None
    assert "plan" not in d.plan("restore")
    toolbar = Node("AXToolbar").add(ordinary)
    d.window.add(Node("AXWebArea").add(toolbar))
    d.session.deadline = 10
    scope, _ = d.session._capture()
    assert not d.session.is_address_bar(ordinary, scope)


@pytest.mark.parametrize("change", ["window", "manual_focus", "removed", "hidden", "new_selection", "cancelled"])
def test_pinned_selection_rejects_stale_context_and_plans(change):
    d, nodes = spatial_desktop()
    entered = select(d)
    pending = select(d, "move", entered["selection"], "down", apply=False)
    if change == "window": d.stamp["window"] = 99
    elif change == "manual_focus": d.app.attrs["AXFocusedUIElement"] = nodes[5]
    elif change == "removed": nodes[pending["index"]-1].parent = None
    elif change == "hidden": nodes[pending["index"]-1].hidden = True
    elif change == "new_selection": select(d)
    else: d.session.handle("focus_selection", action="cancel", selection=entered["selection"])
    before = list(d.writes)
    assert d.apply(pending)["status"] == "stale"
    assert d.writes == before


def test_geometry_refresh_follows_window_and_never_reads_text():
    d, nodes = spatial_desktop()
    result = select(d)
    nodes[0].frame = (50,106,520,46)
    inspected = select(d, "inspect", result["selection"])
    assert inspected["fields"][0]["rect"] == [50,106,520,46]
    assert set(d.reads).isdisjoint({"AXValue", "AXSelectedText", "AXTitle", "AXDescription"})


@pytest.mark.parametrize("bundle,metadata,address", [
    ("com.google.Chrome", {"AXKeyShortcutsValue": "⌘L"}, True),
    ("com.microsoft.edgemac", {"AXDOMClassList": ["OmniboxViewViews"]}, True),
    ("org.mozilla.firefox", {"AXDOMIdentifier": "urlbar-input"}, True),
    ("org.mozilla.firefox", {"AXDOMIdentifier": "searchbar"}, False),
    ("com.google.Chrome", {"AXIdentifier": "find-input"}, False),
    ("com.google.Chrome", {}, False),
    ("com.apple.Safari", {}, True),
    ("com.apple.Safari", {"AXIdentifier": "WEB_BROWSER_ADDRESS_AND_SEARCH_FIELD"}, True),
    ("com.openai.codex", {"AXIdentifier": "address-field"}, False),
])
def test_only_identified_browser_address_fields_defer_focus(bundle, metadata, address):
    d, target, _ = browser_desktop()
    d.app.attrs["bundle"] = bundle
    target.attrs = metadata
    assert select(d, apply=False)["deferred"] is address


def test_restore_keeps_an_explicitly_focused_address_bar():
    d, address, ordinary = browser_desktop()
    d.app.attrs["AXFocusedUIElement"] = address
    for _ in range(3):
        restored = d.plan("restore")
        assert restored["status"] == "focused" and restored["index"] == 1
        assert "plan" not in restored
    assert not d.writes and d.app.attrs["AXFocusedUIElement"] is address


def test_address_suggestion_window_changes_do_not_end_selection_after_down():
    d, address, ordinary = browser_desktop()
    d.app.attrs["AXFocusedUIElement"] = address
    entered = select(d)
    assert entered["index"] == 1 and not d.writes
    d.stamp["window"] = 98  # A suggestion panel moves ahead while selecting.
    focus = d.focus
    def collapse_suggestions(node):
        focus(node)
        d.stamp["window"] = 20
    d.focus = collapse_suggestions
    moved = select(d, "move", entered["selection"], "down")
    assert moved["status"] == "focused" and moved["index"] == 2
    assert moved["stamp"] == d.front() and d.writes == [ordinary]
    d.stamp["window"] = 99  # A panel can finish its closing animation later.
    inspected = select(d, "inspect", entered["selection"])
    assert inspected["status"] == "available" and inspected["index"] == 2
    d.app.attrs["AXFocusedWindow"] = Node("AXWindow", frame=d.window.frame)
    assert select(d, "inspect", entered["selection"])["status"] == "stale"


def test_outline_uses_composer_container_but_focus_stays_on_editor():
    d = Desktop()
    d.a.frame = (112, 114, 712, 44)
    wrapper = Node("AXGroup", frame=(100, 100, 736, 98)).add(d.a)
    d.window.children = []
    d.window.add(Node("AXWebArea", frame=d.window.frame).add(wrapper))
    result = select(d)
    assert result["fields"][0]["rect"] == [100, 100, 736, 98]
    assert result["fields"][0]["control_rect"] == [112, 114, 712, 44]
    assert result["fields"][0]["bounds_source"] == "container"
    assert d.writes == [d.a]
    wrapper.frame = (100, 100, 736, 116)
    updated = select(d, "inspect", result["selection"])
    assert updated["fields"][0]["rect"] == [100, 100, 736, 116]


@pytest.mark.parametrize("kind", ["page", "large_group", "two_fields", "address"])
def test_outline_never_invents_a_missing_border_or_encloses_unrelated_controls(kind):
    d = Desktop()
    d.a.frame = (100, 110, 500, 30)
    wrapper = Node("AXGroup", frame=(90, 100, 520, 80)).add(d.a)
    if kind == "page": wrapper.role = "AXWebArea"
    elif kind == "large_group": wrapper.frame = d.window.frame
    elif kind == "two_fields":
        d.b.frame = (100, 150, 500, 25)
        wrapper.add(d.b)
    else:
        d.app.attrs["bundle"] = "com.apple.Safari"
        d.a.attrs["AXIdentifier"] = "WEB_BROWSER_ADDRESS_AND_SEARCH_FIELD"
    d.window.children = []
    d.window.add(wrapper)
    result = select(d)
    assert result["fields"][0]["rect"] == list(d.a.frame)
    assert result["fields"][0]["bounds_source"] == "control"


def safari_handoff_desktop():
    d, address, target = browser_desktop()
    d.app.attrs["AXFocusedUIElement"] = address
    # All-selection metadata must neither be read nor changed by navigation.
    address.attrs["AXSelectedTextRange"] = (0, 120)
    requested, clicks = [], []
    d.focus = lambda node: requested.append(node)  # Safari acknowledges but keeps address focus.
    d.confirmed_focus = lambda *args: d.app.attrs["AXFocusedUIElement"]
    d.hit_test = lambda point: target
    def click(scope, point, *, expected_focus):
        assert expected_focus is address
        clicks.append((scope.stamp["pid"], point))
        d.app.attrs["AXFocusedUIElement"] = target
    d.click_focus = click
    return d, address, target, requested, clicks


def test_safari_selected_address_hands_off_to_web_editor_once_and_keeps_picker():
    d, address, target, requested, clicks = safari_handoff_desktop()
    entered = select(d)
    assert entered["deferred"] and not requested and not clicks
    moved = select(d, "move", entered["selection"], "down")
    assert moved["status"] == "focused" and moved["focus_method"] == "safari_click"
    assert requested == [target] and clicks == [(10, (300, 230))]
    assert select(d, "inspect", entered["selection"])["index"] == 2
    assert address.attrs["AXSelectedTextRange"] == (0, 120)
    assert "AXSelectedTextRange" not in d.reads
    # Merely selecting the address bar again must not focus/click it.
    assert select(d, "move", entered["selection"], "up")["deferred"]
    assert d.app.attrs["AXFocusedUIElement"] is target and len(clicks) == 1


@pytest.mark.parametrize("block", ["other_app", "non_address", "non_web", "covered", "button", "link",
                                    "no_geometry", "focus_changed", "expired", "moved_overlay", "hidden"])
def test_safari_fallback_does_not_click_unverified_or_changed_targets(block):
    d, address, target, requested, clicks = safari_handoff_desktop()
    entered = select(d)
    if block == "other_app": d.app.attrs["bundle"] = "com.google.Chrome"
    elif block == "non_address":
        address.attrs["AXIdentifier"] = "find-input"
    elif block == "non_web": target.parent.role = "AXGroup"
    elif block == "covered": d.hit_test = lambda _: Node("AXGroup")
    elif block in {"button", "link"}:
        child = Node("AXButton" if block == "button" else "AXLink")
        target.add(child)
        d.hit_test = lambda _: child
    elif block == "focus_changed":
        d.confirmed_focus = lambda *args: Node("AXTextField")
    elif block == "expired":
        def confirm(*args):
            d.now = .81
            return address
        d.confirmed_focus = confirm
    elif block == "moved_overlay":
        hits = iter([target, Node("AXGroup")])
        d.hit_test = lambda _: next(hits)
    elif block == "hidden":
        def hit(_):
            target.hidden = True
            return target
        d.hit_test = hit
    pending = select(d, "move", entered["selection"], "down", apply=False)
    if block == "no_geometry": target.frame = None
    result = d.apply(pending)
    assert result["status"] in {"not_focusable", "stale"}, result
    assert not clicks


def test_safari_successful_ax_focus_needs_no_click_and_failed_click_is_not_success():
    d, address, target, requested, clicks = safari_handoff_desktop()
    d.focus = Desktop.focus.__get__(d)
    entered = select(d)
    assert select(d, "move", entered["selection"], "down")["focus_method"] == "accessibility"
    assert not clicks
    d, address, target, requested, clicks = safari_handoff_desktop()
    d.click_focus = lambda scope, point, **kwargs: clicks.append((scope.stamp["pid"], point))  # No focus change.
    entered = select(d)
    pending = select(d, "move", entered["selection"], "down", apply=False)
    assert d.apply(pending)["status"] == "not_focusable"
    assert d.apply(pending)["status"] == "stale"  # Never replay an attempted click.
    assert len(clicks) == 1
