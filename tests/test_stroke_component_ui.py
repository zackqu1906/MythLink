"""The main palette and trial use the same component encoding and glyph map."""
from proximic_ring.touchpad.stroke.stroke_fuzzy import stroke_evidence
from proximic_ring.touchpad.stroke.stroke_input import StrokeDictionary
from test_inline_ui import inline_ui
from test_stroke_fuzzy_ui import local_editor
from test_complete_stroke_input import load_window


def test_main_palette_strict_components_color_noncontiguous_strokes(inline_ui):
    _, _, _, controller, _ = local_editor(inline_ui)
    controller._dictionary = StrokeDictionary()
    controller.fuzzyMatching = False
    controller._code = 'szhszh'  # Close the outer frame, then write the inner 口.
    controller._stroke_evidence = [stroke_evidence(c) for c in controller._code]
    controller._refresh_candidates()
    match = next(r for r in controller._candidate_matches if r['char'] == '回')
    assert match['match_type'] == 'component' and match['corrections'] == []
    index = controller._candidates.index('回')
    controller._page = index//controller.candidatePageSize
    visual = next(v for v in controller.strokeCandidateVisuals if v['character'] == '回')
    assert visual['available'] and visual['states'] == ['entered']*6


def test_trial_strict_component_prefix_preserves_mapping_after_undo(tmp_path, monkeypatch):
    _, window = load_window(tmp_path, monkeypatch)
    try:
        window.fuzzy_toggle.setChecked(False)
        window.allowed_characters = {'回'}
        window.code = 'szh'
        window.evidence = [stroke_evidence(c) for c in window.code]
        window.refresh()
        row = next(r for r in window.matches if r['char'] == '回')
        assert row['observed_to_expected'][:3] == (0, 1, 5)
        window.undo()
        assert window.code == 'sz'
        assert next(r for r in window.matches if r['char'] == '回')['observed_to_expected'][:2] == (0, 1)
    finally:
        window.close()
