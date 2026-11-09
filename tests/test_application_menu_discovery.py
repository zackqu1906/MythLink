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
    catalog._menu_cache.clear()  # Model startup before the first background scan.
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
    assert catalog.actions == [other] and not catalog.busy
    assert len(requests) == 2


def test_selected_app_read_takes_priority_and_background_failure_continues(route, monkeypatch):
    catalog, requests = prepared(route, monkeypatch)
    candidates(catalog)
    catalog.selectApplication(OTHER)
    complete(catalog, BUNDLE, [], error='Application has no accessible menus')
    assert requests == [('background_menu', BUNDLE), ('menu', OTHER)]
    assert catalog.busy and catalog.selectedApp == OTHER
    assert catalog._menu_cache[BUNDLE]['error'] == 'Application has no accessible menus'
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


@pytest.mark.parametrize('result', [dict(actions=[ACTION]), dict(actions=[]),
    dict(actions=[ACTION], partial=True, unresolved=1)])
def test_switching_apps_reuses_cache_including_empty_and_partial_results(route, monkeypatch, result):
    catalog, requests = prepared(route, monkeypatch)
    catalog._cache_menu(BUNDLE, result)
    catalog._cache_menu(OTHER, dict(actions=[]))
    read_at = catalog._menu_cache[BUNDLE]['read_at']
    for _ in range(5):
        catalog.selectApplication(OTHER)
        catalog.selectApplication(BUNDLE)
        assert not catalog.busy and catalog.actions == result['actions']
        assert catalog.menuReadAt == read_at
        assert catalog.menuState == ('partial' if result.get('partial') else 'ready')
    assert requests == []
    if result['actions']:
        assert catalog.setBinding(BUNDLE, 'circle-clockwise', ACTION['id'])
    candidates(catalog)
    assert requests == []  # Refreshing the app picker also reuses read menus.


def test_manual_refresh_rereads_cached_menu_and_updates_saved_command(route, monkeypatch):
    catalog, requests = prepared(route, monkeypatch)
    catalog._cache_menu(BUNDLE, dict(actions=[ACTION]))
    catalog.selectApplication(BUNDLE)
    assert catalog.setBinding(BUNDLE, 'circle-clockwise', ACTION['id'])
    catalog.refreshMenu()
    assert requests == [('menu', BUNDLE)] and catalog.busy
    updated = dict(ACTION, id='updated-key', shortcut='Cmd+J')
    catalog._apply_result('menu', catalog._generation['menu'], BUNDLE, dict(actions=[updated]), '')
    assert not catalog.busy and catalog.actions == [updated]
    assert catalog.bindings[BUNDLE]['circle-clockwise']['shortcut'] == 'Cmd+J'
    catalog.selectApplication('')
    catalog.selectApplication(BUNDLE)
    assert len(requests) == 1 and catalog.actions == [updated]


def test_failed_first_read_waits_for_manual_refresh_instead_of_retrying_each_switch(route, monkeypatch):
    catalog, requests = prepared(route, monkeypatch)
    catalog.selectApplication(BUNDLE)
    catalog._apply_result('menu', catalog._generation['menu'], BUNDLE, None, '请先打开应用')
    catalog.selectApplication('')
    catalog.selectApplication(BUNDLE)
    assert requests == [('menu', BUNDLE)]
    assert catalog.menuState == 'error' and not catalog.busy and catalog.message == '请先打开应用'
    catalog.refreshMenu()
    assert requests == [('menu', BUNDLE), ('menu', BUNDLE)]
    catalog._apply_result('menu', catalog._generation['menu'], BUNDLE, dict(actions=[ACTION]), '')
    assert catalog.actions == [ACTION] and catalog.menuState == 'ready'


def test_failed_manual_refresh_keeps_previous_cache_for_later_switch(route, monkeypatch):
    catalog, requests = prepared(route, monkeypatch)
    catalog._cache_menu(BUNDLE, dict(actions=[ACTION]))
    catalog.selectApplication(BUNDLE)
    catalog.refreshMenu()
    catalog._apply_result('menu', catalog._generation['menu'], BUNDLE, None, '请先打开应用')
    assert catalog.menuState == 'error'
    catalog.selectApplication('')
    catalog.selectApplication(BUNDLE)
    assert requests == [('menu', BUNDLE)] and catalog.actions == [ACTION] and not catalog.busy
