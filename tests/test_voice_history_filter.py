from proximic_ring.ui.controller import _VoiceHistoryListModel
from proximic_ring.ui.voice_history_filter import VoiceHistoryFilter


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
