"""Live log window: one readable line for each recognition the trial makes."""
from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget

MAX_LINES = 5000
MIN_SIDE_WIDTH = 320
MAX_SIDE_WIDTH = 560
OVERLAY_SIZE = (440, 280)
GAP = 8

STROKES = {'h': '横', 's': '竖', 'p': '撇', 'n': '点', 'z': '折'}
ACTIONS = {'tap': '轻触', 'stroke': '笔画', 'reject': '无法判定', 'select': '选字', 'page': '翻页',
           'punctuation': '标点', 'cancel-selection': '取消选字', 'delete-one': '退一笔', 'clear-all': '清空笔画',
           'ignore': '忽略'}
PHASES = {'holding': '静止等待', 'writing': '书写中', 'selecting': '选字中'}
DIRECTIONS = {'up': '上', 'up-right': '右上', 'right': '右', 'down-right': '右下', 'down': '下',
              'down-left': '左下', 'left': '左', 'up-left': '左上', 'center': '中心'}
REASONS = {'stationary-release': '静止抬手', 'moving-release': '移动后抬手', 'uncertain-motion': '动作不确定',
           'incomplete-contact': '接触不完整', 'invalid-geometry': '轨迹无效', 'invalid-trajectory': '轨迹无效',
           'stationary-hold': '静止停留', 'pause-hold': '书写后停顿', 'up-reserved': '上方向未分配',
           'moving-contact': '接触有明显移动', 'commit-cooldown': '出字后冷却中', 'light-tap': '稳定轻触',
           'disabled': '保护已关闭'}


def _name(table, value):
    return table.get(value, str(value))


def _strokes(code):
    return ''.join(STROKES.get(stroke, stroke) for stroke in code or '') or '空'


def _source(decision):
    return '双指' if decision.get('source') == 'two-finger' else '单指'


def _action(decision):
    """Name a final decision; a raw two-finger decision has only its direction."""
    action = decision.get('action')
    if action is not None:
        return _name(ACTIONS, action)
    return '选字手势' if decision.get('phase') == 'selecting' else '未决定'


def _join(*parts):
    return ' · '.join(part for part in parts if part)


def _contact(values):
    decision = values.get('intent_decision') or {}
    direction, click, duration, path = (decision.get('direction'), values.get('sdk_is_click'),
                                        decision.get('duration_ms'), decision.get('path_length'))
    return '接触', _join(
        _source(decision), _action(decision),
        _name(DIRECTIONS, direction) if direction else '',
        _name(REASONS, decision['reason']) if decision.get('reason') else '',
        f'{duration:.0f} ms' if duration is not None else '',
        f'路径 {path:.1f}' if path is not None else '',
        '' if click is None else 'SDK点击' if click else 'SDK非点击')


def _phase(values):
    phase = values.get('phase')
    if phase == 'idle':  # The contact's final decision follows; idle only adds noise.
        return None
    direction = values.get('direction')
    return '阶段', _join(_source(values), _name(PHASES, phase), _name(DIRECTIONS, direction) if direction else '')


def _decision(values):
    decision = values.get('decision') or {}
    action, baseline = decision.get('action'), decision.get('baselineAction')
    if action == 'stroke':  # The recognised stroke gets its own line next.
        return ('手势', f'复核后不{_name(ACTIONS, baseline)}，保留为笔画') if baseline not in (None, 'stroke') else None
    return '手势', _join(_name(ACTIONS, action), _name(REASONS, decision['reason']) if decision.get('reason') else '')


def _stroke(values):
    result = values['result']
    candidates = ' '.join(item['char'] for item in (values.get('fuzzy_candidates') or [])[:5])
    return '笔画', _join(f"{result['shape']}", f"距离 {result['distance']:.4f}",
                       f"{values.get('recognition_ms', 0):.1f} ms", f"笔序 {_strokes(values.get('code'))}",
                       f'候选 {candidates}' if candidates else '无候选')


def _selection(values):
    decision = values.get('decision') or {}
    target, direction = decision.get('target'), decision.get('direction')
    return '选字', _join(
        _source(decision), _action(decision), _name(DIRECTIONS, direction) if direction else '',
        f'第{target + 1}候选' if isinstance(target, int) else str(target) if target is not None else '')


def _tap(values):
    if values.get('allowed'):
        return '轻触', _join('已放行', f"确认「{values['candidate']}」" if values.get('candidate') else '无候选')
    return '轻触', _join('已拦截', _name(REASONS, values.get('reason')))


FORMATTERS = {
    'contact_intent': _contact,
    'contact_intent_state': _phase,
    'contact_decision': _decision,
    'stroke': _stroke,
    'hold_selection': _selection,
    'tap_confirmation': _tap,
    'commit': lambda v: ('出字', _join(f"「{v.get('character')}」", f"笔序 {_strokes(v.get('code'))}")),
    'punctuation': lambda v: ('出字', _join(f"标点「{v.get('symbol')}」",
                                          f"丢弃笔序 {_strokes(v['discarded_code'])}" if v.get('discarded_code') else '')),
    'delete_one': lambda v: ('删除', f"剩余笔序 {_strokes(v.get('code'))}"),
    'manual_stroke': lambda v: ('笔画', f"手动 {_strokes(v.get('category'))}"),
    'input_focus': lambda v: ('焦点', '窗口已聚焦，戒指输入生效' if v.get('active') else '窗口未聚焦，戒指输入暂停'),
    'sentence_trial_start': lambda v: ('实验', f"开始：{v.get('target')}"),
    'sentence_trial_end': lambda v: ('实验', '结束'),
}


def describe(kind, values):
    """Return (category, text), or None for events that are not recognitions."""
    formatter = FORMATTERS.get(kind)
    return formatter(values) if formatter is not None else None


class StatusLogWindow(QWidget):
    """Never takes activation: the trial pauses ring input when it loses focus."""

    def __init__(self, parent=None):
        super().__init__(parent, Qt.Tool | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setWindowTitle('识别状态日志')
        self.view = QPlainTextEdit(readOnly=True)
        self.view.setMaximumBlockCount(MAX_LINES)
        self.view.setFont(QFontDatabase.systemFont(QFontDatabase.FixedFont))
        self.view.setPlaceholderText('连接戒指后，每次接触的识别结果会显示在这里。')
        self.view.setFocusPolicy(Qt.NoFocus)
        clear = QPushButton('清空')
        clear.setFocusPolicy(Qt.NoFocus)
        clear.clicked.connect(self.view.clear)
        footer = QHBoxLayout()
        hint = QLabel('点击本窗口不会暂停戒指输入')
        hint.setEnabled(False)
        footer.addWidget(hint, 1)
        footer.addWidget(clear)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(GAP, GAP, GAP, GAP)
        layout.addWidget(self.view, 1)
        layout.addLayout(footer)

    def add(self, kind, values):
        """Called from inside the trial's input flow, so a bad event must not raise."""
        try:
            line = describe(kind, values)
        except Exception as error:
            line = '错误', f'{kind} 无法显示：{type(error).__name__}: {error}'
        if line is not None:
            self.view.appendPlainText(f"{datetime.now():%H:%M:%S.%f}"[:-3] + f'  [{line[0]}] {line[1]}')

    def place_beside(self, window):
        """Dock right of the trial when the screen has room, else float in the corner."""
        available = window.screen().availableGeometry()
        frame = window.frameGeometry()
        spare = available.width() - frame.width() - GAP
        if window.isMaximized() or spare < MIN_SIDE_WIDTH:
            width, height = OVERLAY_SIZE
            self.resize(width, height)
            self.move(available.right() - width - 2*GAP, available.bottom() - height - 6*GAP)
            return
        window.move(available.left(), frame.top())
        width = min(MAX_SIDE_WIDTH, spare - 2*GAP)
        self.resize(width, window.height())
        self.move(available.left() + frame.width() + GAP, frame.top())
