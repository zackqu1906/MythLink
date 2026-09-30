from copy import deepcopy
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from proximic_ring.ui.home_statistics import HomeStatistics, summarize_history


NOW = datetime(2026, 9, 29, 12, tzinfo=timezone(timedelta(hours=8)))


def entry(identity="a", **changes):
    return dict(dict(interactionId=identity, createdAt=NOW.isoformat(), mode="dictation",
                     outcome="applied", candidateAvailable=True, candidateText="你好 A！\n",
                     text="不应直接统计的识别原文", durationSeconds=2.5), **changes)


def test_totals_use_applied_dictation_only_and_count_edit_once():
    rows = [entry(), entry("edit", mode="edit", candidateText="已有文档" * 100),
            entry("empty-edit", mode="edit", candidateText="", candidateEmpty=True),
            entry("confirmed", outcome="confirm", candidateText="测试"),
            entry("unknown-mode", mode="")]
    before = deepcopy(rows)
    summary = summarize_history(rows + [rows[0]], now=NOW)
    today = summary["today"]
    assert today["records"] == 5 and today["inputCharacters"] == 6
    assert today["completedOperations"] == 4 and today["voiceSeconds"] == 12.5
    assert summary["total"] == today and summary["yesterday"]["records"] == 0
    assert rows == before


@pytest.mark.parametrize("outcome", ["undone", "native_undo_sent", "cancelled", "cancel", "apply_failed", "abandoned", "no_result", ""])
def test_unsuccessful_or_undone_records_contribute_only_recorded_time(outcome):
    summary = summarize_history([entry(outcome=outcome)], now=NOW)["today"]
    assert summary["inputCharacters"] == summary["completedOperations"] == 0
    assert summary["voiceSeconds"] == 2.5


def test_local_dates_and_invalid_timestamps_have_separate_buckets():
    rows = [entry("today", createdAt="2026-09-28T16:00:00+00:00"),
            entry("yesterday", createdAt="2026-09-28T15:59:59+00:00"),
            entry("local", createdAt="2026-09-29T01:00:00"),
            entry("older", createdAt="2026-09-01T01:00:00+08:00"),
            entry("missing", createdAt=""), entry("invalid", createdAt="not a timestamp")]
    result = summarize_history(rows, now=NOW)
    assert result["today"]["records"] == 2
    assert result["yesterday"]["records"] == 1
    assert result["total"]["records"] == 6 and result["undatedRecords"] == 2


def test_history_dates_use_the_records_dst_offset():
    now = datetime(2026, 3, 9, 0, 30, tzinfo=ZoneInfo("America/New_York"))
    rows = [entry("older", createdAt="2026-03-08T04:30:00Z"),
            entry("yesterday", createdAt="2026-03-08T05:30:00Z")]
    result = summarize_history(rows, now=now)
    assert result["today"]["records"] == 0 and result["yesterday"]["records"] == 1
    assert result["total"]["records"] == 2


def test_missing_results_and_invalid_durations_are_not_invented():
    rows = [entry("missing", candidateAvailable=False, durationSeconds=None),
            entry("nan", durationSeconds=float("nan")),
            entry("inf", durationSeconds=float("inf")),
            entry("negative", durationSeconds=-1),
            entry("zero", durationSeconds=0, candidateText="")]
    result = summarize_history(rows, now=NOW)["today"]
    assert result["missingText"] == 1 and result["missingDuration"] == 4
    assert result["completedOperations"] == 5 and result["inputCharacters"] == 12
    assert result["voiceSeconds"] == 0


def test_replacing_a_record_mode_or_outcome_does_not_double_count():
    row = entry()
    assert summarize_history([row], now=NOW)["today"]["inputCharacters"] == 4
    row.update(mode="edit", candidateText="整段编辑结果")
    assert summarize_history([row], now=NOW)["today"]["inputCharacters"] == 0
    assert summarize_history([row], now=NOW)["today"]["completedOperations"] == 1
    row["outcome"] = "undone"
    assert summarize_history([row], now=NOW)["today"]["completedOperations"] == 0
    assert summarize_history([], now=NOW)["total"]["records"] == 0


def test_visible_statistics_coalesce_updates_and_handle_day_rollover():
    from PySide6.QtCore import QCoreApplication
    from PySide6.QtTest import QTest
    app = QCoreApplication.instance() or QCoreApplication([])
    rows, reads, clock = [entry()], [], [NOW]
    def records():
        reads.append(True)
        return rows
    stats = HomeStatistics(records, clock=lambda: clock[0])
    assert not reads
    stats.active = True
    assert len(reads) == 1 and stats.summary["today"]["inputCharacters"] == 4
    rows.append(entry("second"))
    for _ in range(50):
        stats.historyChanged()
    QTest.qWait(150)
    assert len(reads) == 2 and stats.summary["today"]["inputCharacters"] == 8
    stats.active = False
    rows.pop()
    stats.historyChanged()
    QTest.qWait(150)
    assert len(reads) == 2
    clock[0] += timedelta(days=1)
    stats.active = True
    assert len(reads) == 3 and stats.summary["today"]["records"] == 0
    assert stats.summary["yesterday"]["records"] == 1
    clock[0] += timedelta(days=1)
    stats.refreshDate()
    assert len(reads) == 4 and stats.summary["yesterday"]["records"] == 0
    stats.refreshDate()
    assert len(reads) == 4  # No repeated scans of unchanged history.
    summary = stats.summary
    summary["total"]["records"] = 999
    assert stats.summary["total"]["records"] == 1
    stats.close()
    stats.active = True
    assert not stats.active and not stats._date_timer.isActive()
    assert app is not None
