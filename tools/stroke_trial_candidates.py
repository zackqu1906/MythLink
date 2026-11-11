"""Visual candidate buttons for the standalone trial, with no production UI edits."""
import base64
import sys

from PySide6.QtCore import QByteArray, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QPushButton, QStyle, QStyleOptionButton

from proximic_ring.touchpad.stroke.stroke_glyphs import candidate_glyph, glyph_library
import stroke_trial_menu as trial_menu


def trial_candidate_visuals(window):
    """Use each candidate's accepted order and its own correction positions."""
    matches = {item['char']: item for item in window.matches}
    visuals = []
    offset = window.selected // trial_menu.PAGE_SIZE * trial_menu.PAGE_SIZE
    for character in window.candidates[offset:offset + trial_menu.PAGE_SIZE]:
        match = matches.get(character, {})
        if match.get('match_type') == 'whole_character':
            # Repair indices belong to the expected order, not the observed
            # count: a missing/extra stroke shifts every following position.
            visual = dict(candidate_glyph(character, match['code'], len(match['code']),
                                         corrections=tuple(match['glyph_corrections'])))
            visual['status'] = ('整字纠错 · ' + match['correction_summary'] +
                                (' · ' + visual['status'] if not visual['available'] else ''))
            visuals.append(visual)
            continue
        if match.get('match_type') == 'tolerant':
            if match.get('tolerant_level') == 3:
                visual = dict(candidate_glyph(character, prediction=True), status='猜 · 补位联想')
            else:
                visual = dict(candidate_glyph(character, match['code'], match['prefix_length'],
                                              corrections=tuple(match['glyph_corrections'])))
                visual['status'] = '猜 · ' + (visual['status'] if visual['available'] else '笔顺版本不同')
            visual['guess'] = True
            visuals.append(visual)
            continue
        if match.get('match_type') == 'component':
            fuzzy = tuple(index for index, ink in enumerate(window.evidence)
                          if window.fuzzy_toggle.isChecked() and
                          (ink['ambiguous'] or index in match['corrections']))
            visual = dict(candidate_glyph(character, match['code'], len(window.code), fuzzy,
                stroke_indices=tuple(match['observed_to_expected'])))
            visual['status'] = '组件匹配 · '+visual['status']
            visuals.append(visual)
            continue
        prefix = match.get('logical_prefix', match.get('code', window.code))
        canonical = glyph_library().get(character, {}).get('code', '')
        variants = [row for row in window.dictionary.by_character.get(character, [])
                    if row[1].startswith(prefix)] if prefix else []
        variants.sort(key=lambda row: (row[1] != canonical, row[1] != prefix,
                                      -(row[2] & 1), len(row[1]), row[1]))
        variant = variants[0][1] if variants else prefix
        corrections = set(match.get('corrections', ()))
        fuzzy = (tuple(match.get('glyph_fuzzy', ())) if match.get('match_type') == 'joined_stroke' else
                 tuple(index for index, evidence in enumerate(window.evidence)
                       if window.fuzzy_toggle.isChecked() and (evidence['ambiguous'] or index in corrections)))
        joined_mapping = match.get('observed_to_expected') if match.get('match_type') == 'joined_stroke' else None
        visual = candidate_glyph(character, match['code'] if joined_mapping is not None else variant,
            match.get('logical_entered', len(window.code)), fuzzy, not window.code,
            # Far strokes are also listed as replacements; show each stroke once.
            corrections=tuple(sorted({*match.get('replacement_corrections', ()), *match.get('far_corrections', ())}))
            if window.fuzzy_toggle.isChecked() else (),
            **({'stroke_indices': tuple(joined_mapping)} if joined_mapping is not None else {}))
        if match.get('match_type') == 'joined_stroke':
            visual = dict(visual, status=match.get('joined_label', '连笔 1→2')+' · '+visual['status'])
        if match.get('llm_phrase'):
            visual = dict(visual, status='联想「' + match['llm_phrase'] + '」 · ' + visual['status'])
        visuals.append(visual)
    return visuals


class GlyphCandidateButton(QPushButton):
    """Keep native button interaction while drawing independently colored strokes."""
    def __init__(self, arrow, rank):
        super().__init__()
        self.arrow, self.rank = arrow, rank
        self.visual = None
        self.loading = False
        self.renderer = QSvgRenderer(self)
        self._source = ''
        self.setObjectName('candidate')
        self.setFocusPolicy(Qt.NoFocus)
        self.setMouseTracking(True)
        self.setProperty('directionActive', False)
        self.setProperty('primaryCandidate', rank == 1)

    def sizeHint(self):
        return QSize(154, 154)

    def set_loading(self, loading):
        self.loading = loading
        if loading:
            self.setText('加载中…')
            self.setAccessibleName(f'第{self.rank}候选加载中')
            self.setToolTip('候选正在计算，完成后一次显示')
        self.update()

    def minimumSizeHint(self):
        return QSize(74, 86)

    def set_visual(self, visual):
        self.visual = visual
        source = visual['source'] if visual else ''
        if source != self._source:
            self._source = source
            if source:
                self.renderer.load(QByteArray(base64.b64decode(source.split(',', 1)[1])))
        character = visual['character'] if visual else '—'
        # Preserve button text for accessibility, existing handlers and diagnostics.
        self.setText(f'{self.arrow} {self.rank}\n{character}')
        detail = visual['status'] if visual else '暂无候选'
        if visual and visual['next']:
            detail += ' · 下一笔 ' + visual['next']
        self.setAccessibleName(f'第{self.rank}候选 {character}，{detail}')
        self.setToolTip(f'{self.arrow} 第 {self.rank} 候选「{character}」\n{detail}' if visual else '继续写笔画以显示候选')
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        option = QStyleOptionButton()
        self.initStyleOption(option)
        option.text = ''
        self.style().drawControl(QStyle.CE_PushButton, option, painter, self)
        enabled = self.isEnabled()
        ink = QColor('#345ee5' if self.property('directionActive') else '#71809a' if enabled else '#a7b0c0')
        header = QFont(self.font())
        header.setPixelSize(14 if self.height() >= 115 else 12)
        painter.setFont(header)
        painter.setPen(ink)
        painter.drawText(QRectF(12, 7, self.width() - 24, 21), Qt.AlignLeft | Qt.AlignVCenter, self.arrow)
        painter.drawText(QRectF(12, 7, self.width() - 24, 21), Qt.AlignRight | Qt.AlignVCenter, str(self.rank))
        compact = self.height() < 130
        top, bottom = (22, self.height() - 21) if compact else (27, self.height() - 28)
        side = max(20, min(112, self.width() - 24, bottom - top))
        glyph = QRectF((self.width() - side) / 2, top + (bottom - top - side) / 2, side, side)
        if self.loading:
            font = QFont(self.font())
            font.setPixelSize(15)
            painter.setFont(font)
            painter.setPen(QColor('#738099'))
            painter.drawText(QRectF(4, top, self.width() - 8, bottom - top), Qt.AlignCenter, '加载中…')
        elif self.visual and self._source and self.renderer.isValid():
            # Guesses that only fill an otherwise short page are drawn lighter.
            painter.setOpacity(.5 if self.visual.get('guess') else 1.)
            self.renderer.render(painter, glyph)
            painter.setOpacity(1.)
        else:
            font = QFont('KaiTi' if sys.platform == 'win32' else 'Kaiti SC')
            font.setPixelSize(round(side * .82))
            painter.setFont(font)
            painter.setPen(QColor('#24334d' if enabled else '#c3cbd7'))
            painter.drawText(glyph, Qt.AlignCenter, self.visual['character'] if self.visual else '—')
        footer = QFont(self.font())
        footer.setPixelSize(11 if self.width() >= 105 else 10)
        painter.setFont(footer)
        painter.setPen(ink)
        text = '计算后一次显示' if self.loading else self.visual['status'] if self.visual else '等待输入'
        text = painter.fontMetrics().elidedText(text, Qt.ElideRight, max(0, self.width() - 12))
        painter.drawText(QRectF(6, self.height() - (21 if compact else 24), self.width() - 12, 18), Qt.AlignCenter, text)


class DirectionActionButton(QPushButton):
    """Give punctuation the same hierarchy as the candidate cells."""
    def __init__(self, arrow, symbol, title):
        super().__init__(f'{arrow} {title} {symbol}' if title != '下一页' else '↑\n下一页')
        self.arrow, self.symbol, self.title = arrow, symbol, title
        self.setFocusPolicy(Qt.NoFocus)
        self.setProperty('directionActive', False)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        option = QStyleOptionButton()
        self.initStyleOption(option)
        option.text = ''
        self.style().drawControl(QStyle.CE_PushButton, option, painter, self)
        ink = QColor('#ffffff' if self.property('directionActive') else '#647895')
        painter.setPen(ink)
        font = QFont(self.font())
        font.setPixelSize(14 if self.height() >= 115 else 12)
        painter.setFont(font)
        if self.title != '下一页':
            painter.drawText(QRectF(12, 7, self.width() - 24, 21), Qt.AlignLeft | Qt.AlignVCenter, self.arrow)
        font.setPixelSize(min(46 if self.title != '下一页' else 34, max(22, self.height() // 3)))
        painter.setFont(font)
        painter.drawText(QRectF(6, 25, self.width() - 12, max(22, self.height() - 54)), Qt.AlignCenter, self.symbol)
        font.setPixelSize(12 if self.height() >= 115 else 11)
        painter.setFont(font)
        painter.drawText(QRectF(6, self.height() - 25, self.width() - 12, 18), Qt.AlignCenter, self.title)
