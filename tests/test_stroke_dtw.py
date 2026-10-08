import importlib.util
from pathlib import Path
import sys

import numpy as np

from proximic_ring.stroke_dtw import DTWRecognizer, sequence, distances
from proximic_ring.stroke_input import StrokeDictionary


def test_runtime_matches_experimental_distance():
    tools = Path(__file__).resolve().parents[1]/'tools'
    sys.path.insert(0, str(tools))
    spec = importlib.util.spec_from_file_location('comparison', tools/'benchmark_stroke_models.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    dictionary = StrokeDictionary()
    bank = np.stack([sequence(t['raw_trace']) for t in dictionary.templates[:15]])
    query = sequence([(0, 0), (-50, 100), (-40, 110)])
    expected = module.dtw_matrix(query[None], bank, .15)[0]
    assert np.allclose(distances(query, bank), expected, atol=1e-7)


def test_independent_code_alternatives_and_no_online_learning():
    dictionary = StrokeDictionary()
    recognizer = DTWRecognizer(dictionary.templates, personal=[])
    before = recognizer.bank.copy()
    result = recognizer.recognize([(0, 0), (100, 0), (94, 6)])
    assert result['shape'] == '横钩'
    assert len(result['category_alternatives']) == 2
    assert len({r['category'] for r in result['category_alternatives']}) == 2
    assert np.array_equal(before, recognizer.bank)


def test_dtw_dictionary_stationary_and_horizontal():
    dictionary = StrokeDictionary(recognizer='dtw')
    assert dictionary.recognize([]) is None
    assert dictionary.recognize([(0, 0)]*10) is None
    result = dictionary.recognize([(0, 0), (100, 0)])
    assert result['category'] == 'h' and result['recognizer'] == 'dtw_v1'
    assert result['template_profile'] != 'none'


def test_base_dictionary_can_keep_original_recognizer():
    assert StrokeDictionary().recognize([(0, 0), (100, 0)])['recognizer'] == 'structure_v1'
