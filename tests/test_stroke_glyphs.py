"""Verify real outlines, candidate-specific corrections and rendered UI states."""
import base64
import json
from importlib.resources import files
from pathlib import Path

from PySide6.QtCore import QObject, QPointF, Qt
from PySide6.QtGui import QImage
from PySide6.QtQml import QQmlExpression, qmlContext
from PySide6.QtTest import QTest
import pytest

from proximic_ring.touchpad.stroke.stroke_glyphs import candidate_glyph, glyph_library, stroke_mapping
from proximic_ring.touchpad.stroke.stroke_input import StrokeDictionary
from test_inline_ui import inline_ui
from test_stroke_fuzzy_ui import local_editor, visual_child


def load_offscreen_fonts():
    # Qt's Windows offscreen plugin has no system font discovery. Load installed
    # fonts for screenshot QA only; the production Windows plugin discovers them.
    from PySide6.QtGui import QFontDatabase
    for path in ('C:/Windows/Fonts/msyh.ttc', 'C:/Windows/Fonts/simkai.ttf'):
        if Path(path).exists():
            QFontDatabase.addApplicationFont(path)


def test_offline_outlines_preserve_level_one_and_cover_expansion_examples():
    common = json.loads(files('proximic_ring').joinpath('assets/stroke-common-characters.json').read_text('utf-8'))['characters']
    library = glyph_library()
    assert len(library) == 9574
    assert len(common) == 6500
    assert all(c in library and len(library[c]['code']) == len(library[c]['paths']) for c in common[:3500])
    assert all(c in library and len(library[c]['code']) == len(library[c]['paths']) for c in '霓袅')
    # New scope has characters beyond the outline source. Their normal-font
    # fallback remains selectable; never exclude them just for missing outlines.
    assert not candidate_glyph('拃', 'hshpshshh', 9)['available']


def test_four_states_render_as_real_outline_fills_and_transparency():
    from proximic_ring.ui.touchpad.stroke_glyph_images import native_glyph_images
    glyph = candidate_glyph('木', 'hspn', 2, (1,))
    assert glyph['available'] and glyph['states'] == ['entered', 'fuzzy', 'next', 'remaining']
    png = native_glyph_images([glyph])[0]['png']
    image = QImage.fromData(base64.b64decode(png), 'PNG')
    assert not image.isNull() and image.width() == 192
    pixels = [image.pixelColor(x, y) for y in range(image.height()) for x in range(image.width())]
    assert any(c.name() == '#171c28' and c.alpha() == 255 for c in pixels)
    assert any(c.name() == '#eab308' and c.alpha() == 255 for c in pixels)
    assert any(c.blue() > 200 and c.red() < 50 and 70 < c.alpha() < 150 for c in pixels)
    assert any(c.name() == '#98a1b2' and c.alpha() > 200 for c in pixels)
    assert sum(c.alpha() == 0 for c in pixels) > len(pixels) / 2


def test_undo_completion_prediction_and_unsupported_versions():
    assert candidate_glyph('木', 'hspn', 1)['states'] == ['entered', 'next', 'remaining', 'remaining']
    assert candidate_glyph('木', 'hspn', 4)['states'] == ['entered'] * 4
    prediction = candidate_glyph('木', prediction=True)
    assert prediction['states'] == ['entered'] * 4 and prediction['status'] == '联想'
    unsupported = candidate_glyph('木', 'hsnp', 2)
    assert not unsupported['available'] and unsupported['status'] == '笔顺版本不同'
    assert not candidate_glyph('😀', 'h', 1)['source']
    # The accepted 忄 order must color the left dot and vertical, not both dots.
    reordered = candidate_glyph('快', 'nsnzhpn', 2)
    assert reordered['available']
    assert reordered['states'][:3] == ['entered', 'next', 'entered']
    assert stroke_mapping('nnszhpn', 'nsnzhpn') is None  # No component provenance, no guessed reorder.


@pytest.mark.parametrize('size', [(1440, 940), (940, 700)])
def test_real_candidates_render_page_undo_and_commit(inline_ui, tmp_path, size):
    _, _, root, tp, _ = local_editor(inline_ui)
    load_offscreen_fonts()
    root.resize(*size)
    tp.fuzzyMatching = True
    tp._dictionary = StrokeDictionary()
    tp.addStroke('h')
    first = tp.strokeCandidateVisuals
    assert [v['character'] for v in first] == tp.strokeCandidates
    assert all(v['available'] and v['states'].count('entered') == 1 for v in first if v['source'])
    assert all(v['status'] == '暂缺逐笔字形' for v in first if not v['source'])
    tp.nextCandidates()
    assert tp.candidatePage == 1
    assert [v['character'] for v in tp.strokeCandidateVisuals] == tp.strokeCandidates
    tp.previousCandidates()
    assert tp.strokeCandidateVisuals == first
    tp.addStroke('s')
    item = None
    for _ in range(100):  # The SVG decodes asynchronously; parallel runs can take longer than 80 ms.
        QTest.qWait(20)
        item = visual_child(root.contentItem(), 'strokeCandidateGlyph0')
        if item and QQmlExpression(qmlContext(item), item, 'status === Image.Ready').evaluate()[0]:
            break
    assert item and QQmlExpression(qmlContext(item), item, 'status === Image.Ready').evaluate()[0]
    assert item.isVisible() and item.width() >= 60
    viewport = root.findChild(QObject, 'touchpadViewport')
    viewport.setProperty('contentY', 150)
    QTest.qWait(50)
    assert root.grabWindow().save(str(tmp_path / f'glyph-candidates-{size[0]}.png'))
    tp.undoStroke()
    assert tp.strokeCandidateVisuals == first
    QTest.qWait(60)
    chosen = tp.strokeCandidates[0]
    button = visual_child(root.contentItem(), 'strokeCandidate0')
    point = button.mapToScene(QPointF(button.width()/2, button.height()/2)).toPoint()
    QTest.mouseClick(root, Qt.LeftButton, Qt.NoModifier, point)
    QTest.qWait(30)
    assert root.findChild(QObject, 'strokeTestEditor').property('text') == chosen
    assert not tp._code
    assert all(v['status'] == '联想' for v in tp.strokeCandidateVisuals)


def test_fuzzy_corrections_are_per_candidate_and_follow_page(inline_ui, tmp_path):
    _, _, root, tp, _ = local_editor(inline_ui)
    load_offscreen_fonts()
    characters = '小中是上旧水你他人八'
    tp._dictionary = StrokeDictionary(dict(templates=[], ranks={c: i for i, c in enumerate(characters)},
        entries=[[c, glyph_library()[c]['code'], 1] for c in characters]))
    tp.fuzzyMatching = True
    result = dict(category='s', shape='竖', category_scores=[
        dict(category='s', distance=.001), dict(category='p', distance=.02)])
    tp._append_stroke('s', '竖', result)
    # This is a rescue (not globally ambiguous): only candidates using 撇 are yellow.
    assert not tp._stroke_evidence[0]['ambiguous']
    assert len(tp._candidates) > 5
    seen = set()
    for _ in range(6):
        matches = {item['char']: item for item in tp._candidate_matches}
        for visual in tp.strokeCandidateVisuals:
            if visual['available']:
                correction = bool(matches[visual['character']]['corrections'])
                assert ('fuzzy' in visual['states']) == correction
                seen.add(correction)
        if not tp.moreCandidates:
            break
        tp.nextCandidates()
    assert seen == {False, True}
    tp.clearStrokes()
    tp._dictionary = StrokeDictionary()
    tp.addStroke('p')
    tp._append_stroke('s', '竖', dict(category='s', shape='竖', category_scores=[
        dict(category='s', distance=.001), dict(category='p', distance=.002)]))
    viewport = root.findChild(QObject, 'touchpadViewport')
    viewport.setProperty('contentY', 150)
    QTest.qWait(100)
    assert root.grabWindow().save(str(tmp_path / 'glyph-fuzzy-progress.png'))
    grab = root.findChild(QObject, 'strokeCompositionPanel').grabToImage()
    QTest.qWait(80)
    assert grab.image().save(str(tmp_path / 'glyph-workspace.png'))
    tp.clearStrokes()
    # The left dot is an actual outline in 心, and must remain yellow through completion.
    for code in 'pznn':
        tp._append_stroke(code, '撇', dict(category=code, shape='撇', distance=.001))
    heart = next(v for v in tp.strokeCandidateVisuals if v['character'] == '心')
    assert heart['states'] == ['fuzzy', 'entered', 'entered', 'entered']
    viewport = root.findChild(QObject, 'touchpadViewport')
    viewport.setProperty('contentY', 150)
    QTest.qWait(80)
    assert root.grabWindow().save(str(tmp_path / 'glyph-fuzzy-heart.png'))
    tp.fuzzyMatching = False
    assert '心' not in tp.strokeCandidates
    assert all('fuzzy' not in v['states'] for v in tp.strokeCandidateVisuals)


def test_native_transport_contains_matching_small_pngs(inline_ui):
    c, bridge, _, tp, _ = local_editor(inline_ui)
    tp.addStroke('h')
    inline = c._inline_input
    inline.stroke_target = (inline._epoch, inline._client_id, 'glyph-preview')
    assert inline.stroke_update(tp.strokeCode, tp.strokeCandidates, 0, visuals=tp.strokeCandidateVisuals)
    message = bridge.messages[-1]
    assert [g['character'] for g in message['glyphs']] == message['candidates']
    assert len(json.dumps(message)) < 300_000
    for glyph in message['glyphs']:
        if glyph['png']:
            assert not QImage.fromData(base64.b64decode(glyph['png']), 'PNG').isNull()
        else:
            assert glyph['status'] == '暂缺逐笔字形'
