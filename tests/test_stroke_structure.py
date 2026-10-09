"""Completed-path recognition: sustained tails versus straight/noisy paths."""
from proximic_ring.stroke_input import StrokeDictionary
import pytest


def path(*vertices):
    result = [vertices[0]]
    for a, b in zip(vertices, vertices[1:]):
        result.extend((a[0]+(b[0]-a[0])*i/20, a[1]+(b[1]-a[1])*i/20)
                      for i in range(1, 21))
    return result


def test_short_hook_survives_long_horizontal_first_part():
    dictionary = StrokeDictionary()
    stroke = path((0, 0), (100, 0), (94, 6))
    assert dictionary.recognize_template(stroke)['shape'] == '横'
    assert dictionary.recognize(stroke)['shape'] == '横钩'


def test_short_dian_survives_long_pie_first_part():
    dictionary = StrokeDictionary()
    stroke = path((0, 0), (-50, 100), (-40, 110))
    assert dictionary.recognize(stroke)['shape'] == '撇点'


def test_single_noisy_endpoint_does_not_invent_a_hook():
    dictionary = StrokeDictionary()
    stroke = path((0, 0), (100, 0)) + [(98, 1)]
    assert dictionary.recognize(stroke)['shape'] == '横'
    assert dictionary.recognize(stroke)['structure'] == []


def test_straight_pie_and_real_vertical_hook_remain_distinct():
    dictionary = StrokeDictionary()
    assert dictionary.recognize(path((0, 0), (-30, 100)))['shape'] != '竖钩'
    assert dictionary.recognize(path((0, 0), (0, 100), (-12, 86)))['shape'] == '竖钩'


def test_all_existing_prototypes_keep_their_five_stroke_category():
    dictionary = StrokeDictionary()
    for template in dictionary.templates:
        assert dictionary.recognize(template['trace'])['category'] == template['category'], template['shape']


def test_empty_stationary_and_duplicate_paths():
    dictionary = StrokeDictionary()
    assert dictionary.recognize([]) is None
    assert dictionary.recognize([(0, 0)]*10) is None
    stroke = path((0, 0), (100, 0))
    assert dictionary.recognize([p for p in stroke for _ in range(2)])['shape'] == '横'


@pytest.mark.parametrize('count', [32, 64, 96])
def test_resolutions_use_matching_lengths_and_keep_basic_strokes(count):
    dictionary = StrokeDictionary(sample_count=count)
    assert all(len(trace) == count for trace in dictionary._comparison_traces)
    assert dictionary.recognize(path((0, 0), (100, 0)))['shape'] == '横'
    assert dictionary.recognize(path((0, 0), (100, 0), (94, 6)))['shape'] == '横钩'
    assert dictionary.recognize(path((0, 0), (-50, 100), (-40, 110)))['shape'] == '撇点'


def test_high_resolution_requires_original_templates_and_valid_resolution():
    data = dict(ranks={}, entries=[], templates=[dict(category='h', shape='横', trace=path((0, 0), (1, 0)))])
    with pytest.raises(ValueError, match='original'):
        StrokeDictionary(data, sample_count=64)
    with pytest.raises(ValueError, match='32, 64, or 96'):
        StrokeDictionary(sample_count=48)
