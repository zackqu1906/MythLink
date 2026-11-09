"""Read each configured running app without mixing menus or stealing selection."""
import pytest
from PySide6.QtCore import QCoreApplication

from test_app_gestures import route
from test_application_menus import configure, BUNDLE, ACTION

OTHER = 'com.example.Other'


def prepared(route, monkeypatch):
    _, service, *_ = route
    catalog = configure(service, monkeypatch)
    catalog._candidates.append(dict(value=OTHER, label='Other'))
    catalog.addApplication(OTHER)
    catalog.selectApplication('')
    requests = []
    monkeypatch.setattr(catalog, '_request', lambda *args: requests.append(args))
    return catalog, requests


def candidates(catalog, **kwargs):
    records = [dict(value=BUNDLE, label='Editor', running=True),
               dict(value=OTHER, label='Other', running=True),
               dict(value='not.added', label='Unconfigured', running=True)]
    for item in records:
        item.update(kwargs.get(item['value'], {}))
    catalog._apply_result('apps', catalog._generation['apps'], '', dict(candidates=records), '')


def complete(catalog, bundle, actions, error=''):
    catalog._apply_result('background_menu', catalog._generation['background_menu'], bundle,
                          dict(actions=actions), error)
    QCoreApplication.processEvents()


def test_background_scan_reads_each_added_running_app_and_keeps_current_selection(route, monkeypatch):
    catalog, requests = prepared(route, monkeypatch)
    candidates(catalog)
    assert requests == [('background_menu', BUNDLE)]
    complete(catalog, BUNDLE, [ACTION])
    assert requests[-1] == ('background_menu', OTHER)
    other = dict(ACTION, id='other-action', label='Other command', shortcut='Cmd+J')
    complete(catalog, OTHER, [other])
    assert catalog.selectedApp == '' and catalog.actions == []
    assert set(catalog._menu_cache) == {BUNDLE, OTHER}
    assert len(requests) == 2
    catalog.selectApplication(OTHER)
    assert catalog.actions == [other] and catalog.busy
    assert requests[-1] == ('menu', OTHER)  # Cached choices still get a fresh check.


def test_selected_app_read_takes_priority_and_background_failure_continues(route, monkeypatch):
    catalog, requests = prepared(route, monkeypatch)
    candidates(catalog)
    catalog.selectApplication(OTHER)
    complete(catalog, BUNDLE, [], error='Application has no accessible menus')
    assert requests == [('background_menu', BUNDLE), ('menu', OTHER)]
    assert catalog.busy and catalog.selectedApp == OTHER
    assert catalog._menu_cache.get(BUNDLE, {}).get('actions') == [ACTION]
    catalog._apply_result('menu', catalog._generation['menu'], OTHER, dict(actions=[]), '')
    assert catalog.menuState == 'ready' and catalog.actions == []


@pytest.mark.parametrize('change', ['select', 'remove', 'remove_readd'])
def test_late_background_reply_cannot_change_newer_selection_or_readded_app(route, monkeypatch, change):
    catalog, requests = prepared(route, monkeypatch)
    candidates(catalog)
    if change == 'select':
        catalog.selectApplication(BUNDLE)
    else:
        catalog.removeApplication(BUNDLE)
        if change == 'remove_readd':
            catalog.addApplication(BUNDLE)
    late = dict(ACTION, shortcut='Cmd+J')
    complete(catalog, BUNDLE, [late])
    assert late not in catalog._menu_cache.get(BUNDLE, {}).get('actions', [])
    assert late not in catalog.actions


def test_stopped_or_unconfigured_apps_are_not_launched_for_discovery(route, monkeypatch):
    catalog, requests = prepared(route, monkeypatch)
    candidates(catalog, **{BUNDLE: dict(running=False)})
    assert requests == [('background_menu', OTHER)]
    complete(catalog, OTHER, [])
    assert len(requests) == 1


def test_real_menu_commands_precede_adapter_presets_in_application_category(route, monkeypatch):
    _, service, *_ = route
    bundle = 'com.microsoft.Powerpoint'
    catalog = configure(service, monkeypatch, bundle)
    actions = catalog.actions
    assert actions[0] == ACTION
    assert any(item.get('preset') for item in actions[1:])
