"""Controlled deletions and the actual body-only application request/history path."""
import json
import pytest
from proximic_ring.text_processing.general_edit_prompt import (
    GENERAL_EDIT_CORE, GENERAL_EDIT_VARIANTS, FINE_EDIT_RULES, general_edit_prompt)
from proximic_ring.text_processing.model import (
    ROUTED_EDIT_BODY_VARIANTS, INPUT_ROUTING_ROUTED_EDIT, TextProcessingRequest)
from proximic_ring.text_processing.routed_edit_prompts import routed_edit_prompt_version
from proximic_ring.voice.modification_dataset import ModificationDatasetCollector
from test_routed_edit import settings, processor_returning


@pytest.mark.parametrize('variant', GENERAL_EDIT_VARIANTS)
@pytest.mark.parametrize('body', ['张玮，请今天发给我。', ''])
def test_general_variant_wire_and_history(tmp_path, variant, body):
    processor, calls = processor_returning(body)
    result, raw, model_input, plan = processor.process_routed_edit_with_trace(
        '按要求编辑', settings(), '原文', variant=variant)
    assert result == raw == body and plan is None and len(calls) == 1
    assert variant in ROUTED_EDIT_BODY_VARIANTS
    assert calls[0]['input'][0]['content'][0]['text'] == general_edit_prompt(variant)
    assert json.loads(calls[0]['input'][1]['content'][0]['text']) == model_input
    store = ModificationDatasetCollector(tmp_path, 'test')
    request = TextProcessingRequest(1, 7, 'edit', '按要求编辑', settings(), '原文',
        routing_mode=INPUT_ROUTING_ROUTED_EDIT, routed_edit_variant=variant)
    store.record_text_request(request)
    record = store._interaction_data(store.interaction_id_for_session(7))['llm']['requests'][0]
    assert record['system_prompt'] == general_edit_prompt(variant)
    assert record['prompt_version'] == routed_edit_prompt_version(variant)
    readable = json.dumps(store.load_details(store.interaction_id_for_session(7)), ensure_ascii=False)
    assert '直接生成正文' in readable and '未取得分类' not in readable


def test_deletions_preserve_core_and_every_remaining_block():
    full = general_edit_prompt('general_full_v1')
    for variant, (_, missing) in GENERAL_EDIT_VARIANTS.items():
        expected = full
        for key in missing:
            expected = expected.replace('\n\n' + FINE_EDIT_RULES[key], '')
        assert general_edit_prompt(variant) == expected
        assert general_edit_prompt(variant).startswith(GENERAL_EDIT_CORE)
    assert general_edit_prompt('general_only_v1') == GENERAL_EDIT_CORE
    assert '释字' not in general_edit_prompt('general_no_spelling_v1')
