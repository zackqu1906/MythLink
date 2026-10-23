import json
from pathlib import Path

import pytest

from proximic_ring.voice.hotwords import HotwordStore, HOTWORD_BUDGET, select_hotwords, validated_hotwords
from proximic_ring.text_processing import LLMSettings, OpenAICompatibleTextProcessor
from proximic_ring.text_processing.prompts import HOTWORD_EXTRACTION_PROMPT, EDIT_PROMPT, POLISH_PROMPT


def test_manual_and_independent_corrections_survive_individual_undo(tmp_path):
    store = HotwordStore(tmp_path / "words.json")
    store.learn("edit-one", ["张玮", "Kubernetes"])
    store.learn("edit-two", ["张玮"])
    store.add("Kubernetes")
    store.retract("edit-one")
    assert set(store.snapshot()) == {"张玮", "Kubernetes"}
    store.retract("edit-two")
    assert store.snapshot() == ["Kubernetes"]
    assert HotwordStore(store.path).snapshot() == ["Kubernetes"]


def test_delete_blocks_learning_and_manual_add_allows_it_again(tmp_path):
    store = HotwordStore(tmp_path / "words.json")
    store.learn("one", ["Java"])
    store.remove(store.entries()[0]["id"])
    store = HotwordStore(store.path)
    store.learn("two", ["java"])
    assert store.entries() == []
    store.add("Java")
    store.learn("three", ["java"])
    assert len(store.entries()) == 1 and store.snapshot() == ["Java"]


def test_disabled_word_stays_disabled_and_rename_becomes_manual(tmp_path):
    store = HotwordStore(tmp_path / "words.json")
    store.learn("one", ["张玮"])
    entry_id = store.entries()[0]["id"]
    store.update(entry_id, enabled=False)
    store.learn("two", ["张玮"])
    assert store.snapshot() == []
    store.update(entry_id, word="张炜", enabled=True, pinned=True)
    store.retract("one")
    store.retract("two")
    assert store.snapshot() == ["张炜"] and store.entries()[0]["manual"]
    store.learn("three", ["张玮"])
    assert store.snapshot() == ["张炜"]


def test_snapshots_are_bounded_and_pinning_prioritizes_old_words(tmp_path):
    store = HotwordStore(tmp_path / "words.json")
    for i in range(20):
        store.add(f"专有词汇{i}")
    first = next(e for e in store.entries() if e["word"] == "专有词汇0")
    store.update(first["id"], pinned=True)
    snapshot = store.snapshot()
    assert snapshot[0] == "专有词汇0" and len(snapshot) < 20
    assert sum(len(word.encode("utf-8")) + 2 for word in snapshot) <= HOTWORD_BUDGET
    snapshot.clear()
    assert store.snapshot()
    assert select_hotwords(["Java", "java", None, "", "x" * 41, "张玮"]) == ["Java", "张玮"]


def test_duplicate_evidence_is_idempotent_and_rename_collision_keeps_entries(tmp_path):
    store = HotwordStore(tmp_path / "words.json")
    store.learn("one", ["Java"])
    original = store.path.read_bytes()
    store.learn("one", ["Java"])
    assert store.path.read_bytes() == original
    store.add("Python")
    java = next(e for e in store.entries() if e["word"] == "Java")
    with pytest.raises(ValueError):
        store.update(java["id"], word="python")
    assert set(store.snapshot()) == {"Java", "Python"}


@pytest.mark.parametrize("content", ['not json', '{"version":1,"entries":[{"word":"张玮"}],"blocked":[]}'])
def test_corrupt_dictionary_is_preserved(tmp_path, content):
    path = tmp_path / "words.json"
    path.write_text(content)
    store = HotwordStore(path)
    assert store.load_error and store.snapshot() == []
    with pytest.raises(ValueError):
        store.add("Java")
    assert path.read_text() == content


def test_disk_failure_does_not_publish_unsaved_words(tmp_path, monkeypatch):
    import proximic_ring.voice.hotwords as module
    store = HotwordStore(tmp_path / "words.json")
    store.add("Java")
    def fail(*_):
        raise OSError("disk full")
    monkeypatch.setattr(module.os, "replace", fail)
    with pytest.raises(OSError):
        store.add("Python")
    assert store.snapshot() == ["Java"]
    assert not list(tmp_path.glob(".hotwords-*"))


def test_extractor_validation_keeps_only_words_added_to_the_result():
    assert validated_hotwords(["张玮"], "联系张伟", "联系张玮") == ["张玮"]
    assert validated_hotwords(["张炜"], "联系张伟", "联系张玮") == []
    assert validated_hotwords(["张玮"], "联系张玮", "请联系张玮") == []
    assert validated_hotwords(["张玮"], "联系张伟和张玮", "联系张玮和张玮") == ["张玮"]
    assert validated_hotwords(["周六", "123"], "周五有100人", "周六有123人") == []


def test_extractor_validation_normalizes_deduplicates_and_skips_invalid_items():
    response = [" 张玮 ", "张玮", {"word": "张玮"}, None, 3, "", "a\nb", "x" * 41, "玮"]
    # The overall response is bounded independently of per-word validation.
    with pytest.raises(ValueError):
        validated_hotwords(response, "联系张伟", "联系张玮")
    assert validated_hotwords(response[:8], "联系张伟", "联系张玮") == ["张玮"]
    assert validated_hotwords([response[-1]], "联系张伟", "联系张玮") == []
    for invalid in ("张玮", {"words": ["张玮"]}, None):
        with pytest.raises(ValueError):
            validated_hotwords(invalid, "联系张伟", "联系张玮")


def test_extraction_diagnostics_distinguish_model_empty_from_rejected_candidates(caplog):
    import logging
    caplog.set_level(logging.INFO)
    assert validated_hotwords([], "飞书", "飞鼠") == []
    assert '"reason": "model_empty"' in caplog.records[-1].getMessage()
    assert validated_hotwords(["鼠"], "飞书", "飞鼠") == []
    assert "isolated_character" in caplog.records[-1].getMessage()
    assert validated_hotwords(["张炜"], "联系张伟", "联系张玮") == []
    assert "not_added_to_result" in caplog.records[-1].getMessage()


def test_extract_uses_its_own_prompt_and_small_output_budget(monkeypatch):
    processor = OpenAICompatibleTextProcessor()
    seen = []
    def request(settings, **kwargs):
        seen.append((settings, kwargs))
        return json.dumps(["张玮"])
    monkeypatch.setattr(processor, "_request_chat", request)
    words = processor.extract_hotwords("联系张伟", "王字旁的玮", "联系张玮", LLMSettings(enabled=False, model="test-model"))
    settings, request = seen[0]
    assert words == ["张玮"]
    assert settings.enabled and settings.timeout_s <= 10
    assert request["system_prompt"] == HOTWORD_EXTRACTION_PROMPT
    assert request["system_prompt"] not in (EDIT_PROMPT, POLISH_PROMPT)
    assert request["edit_tool"] is None and request["max_tokens"] == 1024
    assert json.loads(request["user_content"])["before"] == "联系张伟"


def test_explicit_order_is_persisted_and_only_new_words_go_to_front(tmp_path):
    store = HotwordStore(tmp_path / "words.json")
    for word in ["Alpha", "Beta", "Gamma"]:
        store.add(word)
    ids = {e["word"]: e["id"] for e in store.entries()}
    store.move(ids["Alpha"], ids["Gamma"])
    assert store.snapshot() == ["Alpha", "Gamma", "Beta"]
    store.learn("correction", ["Beta", "Delta", "Epsilon"])
    assert store.snapshot() == ["Delta", "Epsilon", "Alpha", "Gamma", "Beta"]
    store.update(ids["Alpha"], enabled=False)
    assert store.snapshot() == ["Delta", "Epsilon", "Gamma", "Beta"]
    assert HotwordStore(store.path).entries() == store.entries()
    store.retract("correction")
    assert [e["word"] for e in store.entries()] == ["Alpha", "Gamma", "Beta"]
    with pytest.raises(ValueError):
        store.move(ids["Beta"], "deleted-id")
    assert [e["word"] for e in store.entries()] == ["Alpha", "Gamma", "Beta"]


def test_budget_is_an_enabled_prefix_and_reorder_changes_request(tmp_path):
    assert select_hotwords(["Alpha", "x" * 20, "Beta"], budget=12) == ["Alpha"]
    store = HotwordStore(tmp_path / "words.json")
    for i in range(12):
        store.add(f"识别术语{i}")
    last = store.entries()[-1]
    assert last["word"] not in store.snapshot()
    store.move(last["id"], store.entries()[0]["id"])
    assert store.snapshot()[0] == last["word"]


def test_v1_migration_preserves_previously_visible_priority(tmp_path):
    store = HotwordStore(tmp_path / "words.json")
    store.add("Old"); store.add("New")
    value = json.loads(store.path.read_text())
    value["version"] = 1
    value["entries"][-1]["pinned"] = True
    store.path.write_text(json.dumps(value))
    migrated = HotwordStore(store.path)
    assert migrated.snapshot() == ["Old", "New"]
    migrated.move(migrated.entries()[0]["id"], "")
    assert HotwordStore(store.path).snapshot() == ["New", "Old"]
    assert json.loads(store.path.read_text())["version"] == 2
