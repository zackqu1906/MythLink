import numpy as np

from proximic_ring.stroke_dtw import DTWRecognizer, sequence, distances
from proximic_ring.stroke_input import StrokeDictionary


def test_runtime_matches_experimental_distance():
    dictionary = StrokeDictionary()
    bank = np.stack([sequence(t['raw_trace']) for t in dictionary.templates[:15]])
    query = sequence([(0, 0), (-50, 100), (-40, 110)])
    # Golden distances from yyf 741517b's independent experimental DTW.
    expected = np.array([1.3306280374526978, 1.6565781831741333, 0.09248575568199158, 0.2060205489397049, 0.16843053698539734, 0.7212477326393127, 0.36421138048171997, 0.23777633905410767, 0.5325075387954712, 0.9356229305267334, 0.3070331811904907, 0.4461664855480194, 0.5493564009666443, 0.33746907114982605, 0.6444001793861389])
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
