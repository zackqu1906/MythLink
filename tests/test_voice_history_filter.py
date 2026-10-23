from proximic_ring.ui.controller import _VoiceHistoryListModel
from proximic_ring.ui.voice.voice_history_filter import VoiceHistoryFilter

import pytest


def test_search_combines_words_type_and_outcome_and_tracks_live_changes():
    rows = [
        dict(interactionId="a", mode="edit", outcome="applied", text="改为明晚", candidateText="明晚开会", application="TextEdit"),
        dict(interactionId="b", mode="dictation", outcome="applied", text="明晚开会", application="微信"),
        dict(interactionId="c", mode="edit", outcome="cancelled", text="改为后天", application="TextEdit"),
    ]
    source = _VoiceHistoryListModel(rows)
    proxy = VoiceHistoryFilter(source)
    assert proxy.count == 3
    proxy.searchText = "  TEXTEDIT 明晚  "
    assert proxy.count == 1
    proxy.modeFilter = "dictation"
    assert proxy.count == 0
    proxy.modeFilter = "edit"
    proxy.outcomeFilter = "completed"
    assert proxy.count == 1
    rows[0] = dict(rows[0], outcome="undone")
    source.replace_entries(rows)
    assert proxy.count == 0  # ENTRY_ROLE dataChanged must invalidate the filter.
    proxy.outcomeFilter = "undone"
    assert proxy.count == 1
    rows[0] = dict(rows[0], outcome="native_undo_sent")
    source.replace_entries(rows)
    assert proxy.count == 1
    proxy.searchText = ".*"
    assert proxy.count == 0  # Literal search, not user-supplied regular expressions.
    proxy.searchText = ""
    proxy.modeFilter = "all"
    proxy.outcomeFilter = "cancelled"
    assert proxy.count == 1
    source.replace_entries(rows[:-1])
    assert proxy.count == 0
    assert source.rowCount() == 2


def test_search_matches_results_summary_and_original_without_mutating_rows():
    rows = [dict(interactionId="a", text="指令", editBeforeText="原文字", editSummary="替换摘要",
                 candidateText="结果", mode="edit", outcome="applied")]
    source = _VoiceHistoryListModel(rows)
    proxy = VoiceHistoryFilter(source)
    for text in ("原文字", "摘要", "结果", "指令"):
        proxy.searchText = text
        assert proxy.count == 1
    assert source.data(source.index(0, 0), source.ENTRY_ROLE) == rows[0]


def test_record_type_application_and_time_filters_intersect_and_keep_live_selection():
    from datetime import datetime, timedelta, timezone
    from PySide6.QtCore import QCoreApplication
    app = QCoreApplication.instance() or QCoreApplication([])
    now = datetime(2026, 10, 12, 13, 0, tzinfo=timezone(timedelta(hours=8)))
    rows = [
        dict(interactionId='a', mode='dictation', processingMode='raw', application='微信', createdAt=now.isoformat()),
        dict(interactionId='b', mode='dictation', processingMode='polish', application='微信', createdAt=(now-timedelta(days=1)).isoformat()),
        dict(interactionId='c', mode='edit', processingMode='edit', application='Codex', createdAt=(now-timedelta(days=5)).isoformat()),
        dict(interactionId='d', mode='dictation', application='', createdAt='invalid'),
    ]
    source = _VoiceHistoryListModel(rows)
    proxy = VoiceHistoryFilter(source)
    proxy._now = lambda: now
    proxy.applicationFilter = '微信'
    assert proxy.count == 2
    proxy.modeFilter = 'polish'
    assert proxy.count == 1
    proxy.timeFilter = 'today'
    assert proxy.count == 0
    proxy.timeFilter = 'week'
    assert proxy.count == 1
    proxy.modeFilter = 'dictation'
    assert proxy.count == 1
    rows.append(dict(rows[0], interactionId='new', application='TextEdit'))
    source.replace_entries(rows)
    assert proxy.count == 1 and proxy.applicationFilter == '微信'
    assert any(option['value'] == 'TextEdit' for option in proxy.applicationOptions)
    source.replace_entries(rows[2:])
    assert proxy.count == 0 and proxy.applicationFilter == '微信'
    assert any(option['value'] == '微信' for option in proxy.applicationOptions)
    proxy.applicationFilter = 'all'
    proxy.modeFilter = 'all'
    assert proxy.count == 2  # Invalid dates excluded only when time is filtered.
    proxy.timeFilter = 'all'
    proxy.applicationFilter = '__unknown__'
    assert proxy.count == 1
    proxy.applicationFilter = 'missing option'
    proxy.modeFilter = 'bad mode'
    proxy.timeFilter = 'bad time'
    assert proxy.applicationFilter == '__unknown__' and proxy.modeFilter == proxy.timeFilter == 'all'
    assert app is not None


def test_record_types_are_exclusive_and_follow_live_polishing():
    rows = [
        dict(interactionId='raw', mode='dictation', processingMode='raw'),
        dict(interactionId='polished', mode='dictation', processingMode='polish'),
        dict(interactionId='command', mode='edit', processingMode='edit'),
        dict(interactionId='legacy', mode='dictation'),
    ]
    source = _VoiceHistoryListModel(rows)
    proxy = VoiceHistoryFilter(source)
    def ids():
        return {proxy.data(proxy.index(row, 0), proxy.ENTRY_ROLE)['interactionId']
                for row in range(proxy.rowCount())}
    for kind, expected in [('dictation', {'raw', 'legacy'}), ('polish', {'polished'}),
                           ('edit', {'command'})]:
        proxy.modeFilter = kind
        assert ids() == expected
    proxy.modeFilter = 'dictation'
    rows[0] = dict(rows[0], processingMode='polish')
    source.replace_entries(rows)
    assert ids() == {'legacy'}
    proxy.modeFilter = 'polish'
    assert ids() == {'raw', 'polished'}
    proxy.modeFilter = 'all'
    assert len(ids()) == 4


def test_time_filter_uses_local_calendar_boundaries_and_excludes_future_dates():
    from datetime import datetime, timedelta, timezone
    from PySide6.QtCore import QCoreApplication
    app = QCoreApplication.instance() or QCoreApplication([])
    now = datetime(2026, 10, 12, 13, 0, tzinfo=timezone(timedelta(hours=8)))
    stamps = ['2026-10-11T16:00:00+00:00', '2026-10-11T15:59:59+00:00',
              '2026-10-06T00:00:00+08:00', '2026-10-05T23:59:59+08:00',
              '2026-09-13T00:00:00+08:00', '2026-09-12T23:59:59+08:00',
              '2026-10-12T13:00:01+08:00', 'bad']
    source = _VoiceHistoryListModel([dict(interactionId=str(i), createdAt=stamp) for i,stamp in enumerate(stamps)])
    proxy = VoiceHistoryFilter(source)
    proxy._now = lambda: now
    for period, count in [('today', 1), ('week', 3), ('month', 5), ('all', 8)]:
        proxy.timeFilter = period
        assert proxy.count == count
    assert app is not None


def test_unrecognized_and_interrupted_results_do_not_mislabel_cancellation():
    source = _VoiceHistoryListModel([
        dict(interactionId='a', recognized=False, outcome='recognized'),
        dict(interactionId='b', recognized=False, outcome='cancelled'),
        dict(interactionId='c', recognized=False, outcome='interrupted'),
    ])
    proxy = VoiceHistoryFilter(source)
    for outcome in ['unrecognized', 'cancelled', 'interrupted']:
        proxy.outcomeFilter = outcome
        assert proxy.count == 1


@pytest.mark.parametrize('mode,requests,expected', [
    ('dictation', [], 'raw'),
    ('dictation', [{'mode': 'polish', 'status': 'completed'}], 'polish'),
    ('dictation', [{'mode': 'dictation', 'status': 'failed'}], 'polish'),
    ('dictation', [{'mode': 'edit', 'status': 'completed'}], 'raw'),
    ('dictation', [{'mode': 'polish', 'status': 'cancelled'}], 'raw'),
    ('edit', [], 'edit'),
])
def test_processing_mode_is_projected_from_record_evidence(tmp_path, mode, requests, expected):
    from proximic_ring.voice.modification_dataset import ModificationDatasetCollector
    (tmp_path / 'audio.wav').touch()
    record = dict(mode={'selected': mode}, asr={'final_text': '原文'}, llm={'requests': requests})
    entry = ModificationDatasetCollector._history_entry(tmp_path, record)
    assert entry['processingMode'] == expected
    assert record['mode'] == {'selected': mode}
