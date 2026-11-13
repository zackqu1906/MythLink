"""GUI lifecycle for the existing Ring session; high-rate events never enter Qt."""
from __future__ import annotations

import gc
from functools import cached_property
import math
import platform
import sys
import time
from types import SimpleNamespace

import html
from pathlib import Path

from PySide6.QtCore import QObject, Property, QSettings, QStandardPaths, Qt, Signal, Slot
from PySide6.QtGui import QGuiApplication

from ...touchpad.touchpad_mouse import TouchpadMouseOutput
from ...touchpad.touchpad_clicks import TouchpadClicks
from ...touchpad.touchpad_diagnostics import TouchpadDiagnostics
from ...touchpad.click_diagnostics import ClickTrace
from ...touchpad.touchpad_strokes import TouchpadStrokeOutput
from ...touchpad.stroke.stroke_input import StrokeDictionary, SYMBOLS, NAMES
from ...touchpad.stroke.stroke_display import TRAIL_STYLE, normalized_trace
from ...touchpad.stroke.stroke_common import common_characters
from ...platforms.macos.mac_permissions import MacPermissionError
from ...gestures.firmware_gestures import SINGLE_INDEX_WAIT_S
from ...touchpad.stroke.stroke_fuzzy import stroke_evidence, FuzzyPolicy, retune_evidence, ADAPTIVE_SETTINGS
from ...touchpad.stroke.stroke_personal import PersonalStrokeModel, TextLearningHistory, capture_ink, profile_key, qt_text_index
from ...touchpad.stroke.stroke_context import ranking_snapshot
from ...touchpad.stroke.stroke_menu import (PAGE_SIZE, PUNCTUATION_CELLS, TRIAL_SELECTION, TWO_FINGER_SELECTION, trial_contact,
                                             trial_selection, two_finger_selection)
from ...touchpad.stroke.stroke_contact_intent import CONTACT_INTENT

PUNCTUATION = tuple(symbol for _, _, _, symbol, _ in PUNCTUATION_CELLS)
TAP_ACTIONS = {"space", "candidate"}
from ...touchpad.stroke.stroke_test_gestures import recognize as contact_gesture
from ...touchpad.stroke.stroke_tap_guard import TapConfirmationGuard
from ...touchpad.stroke.stroke_experiment import SentenceTrial, write_report
from ...touchpad.stroke.stroke_tolerant import fill_page, index_for
from ...touchpad.stroke.stroke_vectors import StrokeVectorModel, stroke_vector
from ...touchpad.stroke.stroke_word_correction import merge_candidates
from .stroke_correction import StrokeCorrection

REPAIRS = ('whole_character', 'tolerant')  # Suggested by repair, not by the written strokes.
TRIAL_IDLE_HINT = '连接并预热后即可自由书写。点击“开始实验”清空并计时；文本完全一致时自动结束。'


class TouchpadController(QObject):
    changed = Signal()
    selectionChanged = Signal()
    tutorialChanged = Signal()
    selectionCommitted = Signal(str)  # Menu direction a two-finger swipe just confirmed.
    localCharacterCommitted = Signal(str)
    localBackspaceRequested = Signal()
    localDeleteBeforeRequested = Signal()
    localEditorClearRequested = Signal()
    strokeTraceChanged = Signal(list, bool)
    sourceReady = Signal(object, object)
    _event = Signal(object, str, object)

    def __init__(self, owner, *, output_factory=TouchpadMouseOutput):
        super().__init__(owner)
        self.owner, self.output_factory = owner, output_factory
        self._source = self._connection = self._run = None
        self._capture_target = None  # Temporary screenshot owner; shares this pointer stream.
        self._capture_epoch = 0
        self._closed = False
        self._state = "idle"
        self._last_logged_state = None
        self._message = "开启后，用 Ring 移动系统指针并轻触点击。"
        self._gain = self._number("gain", 1., .25, 3.)
        owner._settings.remove("touchpad/seconds")
        self._clicks = owner._settings.value("touchpad/clicks", True, type=bool)
        self._double_click = owner._settings.value("touchpad/doubleClick", True, type=bool)
        self._dwell_drag = owner._settings.value("touchpad/dwellDrag",
            owner._settings.value("touchpad/tapDrag", True, type=bool), type=bool)
        if owner._settings.contains("touchpad/tapDrag"):
            owner._settings.setValue("touchpad/dwellDrag", self._dwell_drag)
            owner._settings.remove("touchpad/tapDrag")
        self._dwell_ms = round(self._number('dwellMs', 400, 300, 600))
        self._right_click = owner._settings.value("touchpad/rightClick", True, type=bool)
        self._scroll = owner._settings.value("touchpad/scroll", True, type=bool)
        self._scroll_gain = self._number('scrollGain', 1., .25, 3.)
        self._invert_scroll = owner._settings.value("touchpad/invertScroll", False, type=bool)
        self._horizontal_swipe = owner._settings.value("touchpad/horizontalSwipe", True, type=bool)
        self._invert_y = owner._settings.value("touchpad/invertY", False, type=bool)
        self._warm = False
        self.stroke_output_factory = TouchpadStrokeOutput
        self._mode = "pointer"
        self._single_finger_action = str(owner._settings.value('touchpad/singleFingerAction', 'space'))
        self._two_finger_action = str(owner._settings.value('touchpad/twoFingerAction', 'candidate'))
        if self._single_finger_action not in TAP_ACTIONS: self._single_finger_action = 'space'
        if self._two_finger_action not in TAP_ACTIONS: self._two_finger_action = 'candidate'
        for key in ("touchpad/inputMode", "touchpad/strokeGesture", "touchpad/strokeEnabled"):
            owner._settings.remove(key)
        self._dictionary = self._predictor = None
        self._personal_enabled = owner._settings.value('touchpad/personalLearning', True, type=bool)
        self._personal_name = str(owner._settings.value('touchpad/writingProfile', '默认'))[:24]
        self._personal_model = None
        self._personal_history = TextLearningHistory()
        self._personal_text = ''
        self._personal_cursor = 0
        self._code = self._last_stroke = self._commit_message = ""
        self._stroke_evidence = []
        self._candidate_matches = []
        self._fuzzy_matching = owner._settings.value("touchpad/fuzzyMatching", True, type=bool)
        self._tutorial_open = owner._settings.value("touchpad/strokeTutorialOpen", True, type=bool)
        self._fuzzy_policy = FuzzyPolicy(round(self._number('fuzzyStrength', 50, 0, 100)))
        self._candidates = []
        self._selected = self._page = 0
        self._pending_stroke_commit = ""
        self._local_stroke_input = self._prediction_mode = False
        self._stroke_context = ""
        self._stroke_gate = self._voice_was_enabled = False
        self._automatic_restart_blocked = False
        self._voice_gate = False
        self._standard_trace = False
        self._tap_commit = None
        self._local_stroke_focus = False
        self._local_input_since = None
        self._selection_state = {}
        self._tap_guard = TapConfirmationGuard()
        self._correction = StrokeCorrection(self._can_apply_correction, self)
        self._correction.changed.connect(lambda _: self._refresh_candidates())
        self._skip_commit_learning = False
        self._trial = None
        self._trial_hint = ''
        self._trial_target = str(owner._settings.value('touchpad/trialTarget', '今天我用戒指输入一句话。'))
        self.trial_report_dir = None  # Defaults to the app data folder.
        self._visibility_epoch = 0
        app = QGuiApplication.instance()
        # A headless QCoreApplication has no application state to follow.
        if app is not None and hasattr(app, 'applicationStateChanged'):
            app.applicationStateChanged.connect(self._visibility_changed)
        from .stroke_session_controller import StrokeSessionController
        self.strokeSession = StrokeSessionController(self)
        owner._inline_input.strokeCommitted.connect(self._stroke_committed)
        owner._inline_input.strokeInvalidated.connect(self._invalidate_strokes)
        self.sourceReady.connect(self.attach, Qt.QueuedConnection)
        self._event.connect(self._receive, Qt.QueuedConnection)
        owner.connectedChanged.connect(self._connection_changed)
        owner.busyChanged.connect(self.changed)
        owner.gestureSettingsChanged.connect(self.changed)
        self.changed.connect(self._log_state)

    def _log_state(self):
        state = (self._state, self._mode)
        if state != self._last_logged_state:
            self._last_logged_state = state
            self.owner._event_log("TOUCHPAD_STATE", _live_message="", state=self._state,
                                  mode=self._mode,
                                  enabled=self.owner._settings.value("touchpad/enabled", True, type=bool),
                                  error=self._message if self._state == "error" else "")

    def _number(self, key, default, low, high):
        try:
            value = float(self.owner._settings.value("touchpad/" + key, default))
            return max(low, min(high, value)) if math.isfinite(value) else default
        except (ValueError, TypeError):
            return default

    def _visibility_changed(self, state):
        self._visibility_epoch += 1
        self._local_input_since = time.monotonic() if state == Qt.ApplicationActive and self._local_stroke_focus else None
        if self._run is not None:
            self.owner._event_log('TOUCHPAD_VISIBILITY', _live_message='',
                                  foreground=state == Qt.ApplicationActive, mode=self._mode)

    @Property(bool, constant=True)
    def supported(self): return sys.platform in {"darwin", "win32"}

    @Property(bool, constant=True)
    def externalStrokeSupported(self): return sys.platform == "darwin"

    @Property(bool, constant=True)
    def macPermissionsRequired(self): return sys.platform == "darwin"

    @Property(bool, notify=changed)
    def raw2Supported(self):
        if sys.platform == 'darwin':
            return platform.machine() == 'arm64'
        from ring_python_sdk.touchpad.raw2_model import windows_raw2_enabled
        if sys.platform != 'win32' or not windows_raw2_enabled():
            return False
        # Windows keeps the legacy touchpad for public firmware (1.2.133,
        # 2.0.2 ...) instead of failing Raw2 START; macOS policy is unchanged.
        probe = getattr(self._source, 'connected_raw2_firmware', None)
        return probe is None or probe() is not False

    @Property(bool, notify=changed)
    def available(self):
        return (self.supported and not self._closed and self.owner.connected
                and not self.owner.busy and self._source is not None
                and self._connection is self.owner._disconnect_event
                and not self._connection.is_set())

    @Property(bool, notify=changed)
    def active(self): return self._state in {"starting", "running"}

    @Property(bool, notify=changed)
    def busy(self): return self._state in {"starting", "stopping"}

    @Property(bool, notify=changed)
    def automaticStartAllowed(self): return not self._automatic_restart_blocked

    @Property(str, notify=changed)
    def state(self): return self._state

    @Property(str, notify=changed)
    def title(self):
        return {"starting": "正在开启触摸板", "running": "触摸板已开启",
                "stopping": "正在停止触摸板", "error": "触摸板未能运行"}.get(self._state, "触摸板已关闭")

    @Property(str, notify=changed)
    def message(self): return self._message

    @Property(str, notify=changed)
    def inputMode(self): return self._mode

    @Property(str, notify=changed)
    def strokeGestureLabel(self): return "双指双击" if self.raw2Supported else "Touchpad 双击"

    @Property(str, notify=changed)
    def singleFingerAction(self): return self._single_finger_action

    @singleFingerAction.setter
    def singleFingerAction(self, value): self._set_tap_action('_single_finger_action', 'singleFingerAction', value)

    @Property(str, notify=changed)
    def twoFingerAction(self): return self._two_finger_action

    @twoFingerAction.setter
    def twoFingerAction(self, value): self._set_tap_action('_two_finger_action', 'twoFingerAction', value)

    @Property(str, notify=changed)
    def strokeTapHint(self):
        label = {'space': '空格', 'candidate': '首选'}
        return '单指' + label[self._single_finger_action] + ' · 双指' + label[self._two_finger_action]

    def _set_tap_action(self, attribute, key, value):
        if value not in TAP_ACTIONS or getattr(self, attribute) == value:
            return
        setattr(self, attribute, value)
        self.owner._settings.setValue('touchpad/' + key, value)
        if self.strokeSession.phase == 'running' and not self.strokeSession.local:
            self.owner._inline_input.send_stroke('stroke_gestures', tap_hint=self.strokeTapHint)
        self.changed.emit()

    def _perform_tap(self, fingers):
        if self._pending_stroke_commit or not self.strokeSession.allow_input():
            return
        action = self._two_finger_action if fingers == 2 else self._single_finger_action
        if action == 'candidate':
            self.selectCandidate(0)
        else:
            self._insert_symbol(' ')

    @Property(int, constant=True)
    def doubleClickIntervalMs(self): return round(TouchpadClicks.interval * 1000)

    @Property(bool, notify=changed)
    def strokePreparing(self):
        return self.strokeSession.engaged and self.strokeSession.phase != "running"

    @Slot(bool)
    def setLocalStrokeFocus(self, focused):
        self._local_stroke_focus = bool(focused)
        self._local_input_since = time.monotonic() if focused else None
        if not focused and self.strokeSession.engaged and self.strokeSession.local:
            self.strokeSession.cancel("已离开试写框，笔画输入已退出")

    @Property(str, notify=changed)
    def strokeCode(self): return " ".join(SYMBOLS[x] for x in self._code)

    @Property(bool, notify=changed)
    def strokeCommitPending(self): return bool(self._pending_stroke_commit)

    @Property(bool, notify=changed)
    def fuzzyMatching(self): return self._fuzzy_matching

    @fuzzyMatching.setter
    def fuzzyMatching(self, enabled):
        if self._pending_stroke_commit:
            return
        self._fuzzy_matching = bool(enabled)
        self.owner._settings.setValue("touchpad/fuzzyMatching", self._fuzzy_matching)
        self._refresh_candidates()

    @Property(bool, notify=tutorialChanged)
    def strokeTutorialOpen(self): return self._tutorial_open

    @strokeTutorialOpen.setter
    def strokeTutorialOpen(self, value):
        if bool(value) == self._tutorial_open:
            return
        self._tutorial_open = bool(value)
        self.owner._settings.setValue("touchpad/strokeTutorialOpen", self._tutorial_open)
        self.tutorialChanged.emit()

    @Property(list, notify=changed)
    def strokeTokens(self):
        # Repairs are merged into the candidate list, so rows are found by character.
        rows = {item['char']: item for item in self._candidate_matches}
        visible = [rows[char] for char in self.strokeCandidates if char in rows]
        corrected = {index for item in visible if item.get('match_type') not in REPAIRS
                     for index in item.get('corrections', ())} if self._fuzzy_matching else set()
        first = visible[0] if visible else {}
        replacements = set(first.get('corrections', ()) if first.get('match_type') == 'whole_character'
                           else first.get('replacement_corrections', ()) if self._fuzzy_matching else ())
        return [dict(symbol=SYMBOLS[letter].replace('𠃍', '乛'), fuzzy=self._fuzzy_matching and
                     ((index < len(self._stroke_evidence) and self._stroke_evidence[index]['ambiguous'])
                      or index in corrected), **({'correction': True} if index in replacements else {}))
                for index, letter in enumerate(self._code)]

    @Property(int, notify=changed)
    def fuzzyStrength(self): return self._fuzzy_policy.level

    @fuzzyStrength.setter
    def fuzzyStrength(self, level):
        if self._pending_stroke_commit:
            return
        policy = FuzzyPolicy(max(0, min(100, int(level))))
        if policy == self._fuzzy_policy:
            return
        self._fuzzy_policy = policy
        self._stroke_evidence = retune_evidence(self._stroke_evidence, policy)
        self.owner._settings.setValue('touchpad/fuzzyStrength', policy.level)
        self.owner._event_log('STROKE_FUZZY_SETTINGS', _live_message='', policy=policy.snapshot(),
                             adaptive_policy=ADAPTIVE_SETTINGS,
                             adaptations=[stroke.get('adaptation') for stroke in self._stroke_evidence])
        self._refresh_candidates()

    @Property(str, notify=changed)
    def adaptiveFuzzyStatus(self):
        if not self._fuzzy_matching:
            return '严格匹配 · 自动容错已关闭'
        stroke = self._stroke_evidence[-1] if self._stroke_evidence else {}
        adjustment = stroke.get('adaptation', {})
        if adjustment.get('applied'):
            metrics = stroke['kinematics']
            return (f"本笔模糊 {adjustment['baseLevel']} → {adjustment['effectiveLevel']} · "
                    f"长度 {metrics['length']:.1f} · {metrics['duration_ms']:.0f} ms · "
                    f"速度 {metrics['speed']:.0f}/s（SDK单位）")
        return '短／快笔自动增加容错 · 单笔最高增加30档'

    def _clear_stroke_evidence(self):
        self._stroke_evidence = []
        self._candidate_matches = []
        self._skip_commit_learning = False

    def _writer_model(self):
        if self._personal_model is None:
            payload = self.owner._settings.value('touchpad/writers/' + profile_key(self._personal_name), '')
            self._personal_model = PersonalStrokeModel(payload)
        return self._personal_model

    def _save_writer(self):
        try:
            self.owner._settings.setValue('touchpad/writers/' + profile_key(self._personal_name), self._writer_model().dumps())
            self.owner._settings.sync()
            if self.owner._settings.status() == QSettings.Status.NoError:
                return
        except (OSError, ValueError):
            pass
        if self._personal_model is not None:
            self._writer_model().error = '个人档案保存失败，本次学习仅在当前运行中有效'

    @Property(bool, notify=changed)
    def personalLearning(self): return self._personal_enabled

    @personalLearning.setter
    def personalLearning(self, enabled):
        if self._code or self._pending_stroke_commit:
            return
        self._personal_enabled = bool(enabled)
        self.owner._settings.setValue('touchpad/personalLearning', self._personal_enabled)
        if self._dictionary is not None:
            self._dictionary.personal_model = self._writer_model() if enabled else None
        self.changed.emit()

    @Property(str, notify=changed)
    def writingProfile(self): return self._personal_name

    @Slot(str)
    def setWritingProfile(self, name):
        name = name.strip()[:24] or '默认'
        if self._code or self._pending_stroke_commit or name == self._personal_name:
            return
        self._personal_name = name
        self.owner._settings.setValue('touchpad/writingProfile', name)
        self._personal_model = None
        self._personal_history = TextLearningHistory()
        if self._dictionary is not None:
            self._dictionary.personal_model = self._writer_model() if self._personal_enabled else None
        self.changed.emit()

    @Property(str, notify=changed)
    def personalLearningStatus(self):
        if not self._personal_enabled:
            return '个人学习已关闭 · 使用通用识别'
        snapshot = self._writer_model().snapshot()
        return snapshot['error'] or f"已学习 {snapshot['confirmations']} 次选字 · {snapshot['samples']} 个笔画样本"

    @Slot()
    def undoPersonalLearning(self):
        if self._code or self._pending_stroke_commit:
            return
        if self._writer_model().retract():
            self._save_writer()
        self.changed.emit()

    @Slot()
    def clearPersonalLearning(self):
        if self._code or self._pending_stroke_commit:
            return
        self._writer_model().clear()
        self._personal_history = TextLearningHistory()
        self._save_writer()
        self.changed.emit()

    @Slot(str, int)
    def observeStrokeEditor(self, text, cursor):
        changed = text != self._personal_text
        self._personal_text, self._personal_cursor = text, qt_text_index(text, cursor)
        if self._personal_model is not None and self._personal_history.reconcile(self._personal_model, text):
            self._save_writer()
            self.changed.emit()
        elif changed and self._trial is not None and self._trial.active:
            self.changed.emit()  # Target progress follows the editor.

    def _learn_writer(self, char, evidence):
        if not self._personal_enabled:
            return
        if self._local_stroke_input and self._personal_text[max(0, self._personal_cursor-len(char)):self._personal_cursor] != char:
            return  # A local view must actually acknowledge insertion before learning.
        token = self._writer_model().learn(self._stroke_dictionary(), char, evidence)
        if token:
            if self._local_stroke_input:
                self._personal_history.track(token, self._personal_text, self._personal_cursor)
            self._save_writer()
            self.owner._event_log('STROKE_PERSONAL_LEARNED', _live_message='',
                                  profile=self._personal_name, **self._writer_model().snapshot())

    @Property(int, notify=changed)
    def candidatePageSize(self): return PAGE_SIZE  # Local trial and macOS palette share the 3×3 menu.

    @Property('QVariantMap', notify=selectionChanged)
    def selectionState(self): return self._selection_state

    @Slot(str)
    def insertPunctuation(self, symbol):
        if symbol in PUNCTUATION:
            self._insert_symbol(symbol)

    def _insert_symbol(self, symbol):
        if symbol not in (*PUNCTUATION, ' ') or not self.strokeSession.allow_input() or self._pending_stroke_commit:
            return
        if self._speech_busy():
            return
        if symbol == ' ' and self.strokeCandidates:
            self._select_candidate(0, suffix=' ')
            return
        before = self._personal_text
        if self._commit_character(symbol):
            if self._local_stroke_input:
                self._tap_guard.committed()
                trial = self._active_trial()
                if trial is not None:
                    trial.commit(len(before), symbol, self._personal_text == before + symbol, source='punctuation')
            self.clearStrokes()
            self._check_trial_complete()

    @Property(list, notify=changed)
    def strokeCandidates(self): return self._candidates[self._page * self.candidatePageSize:self._page * self.candidatePageSize + self.candidatePageSize]

    @Property(list, notify=changed)
    def strokeCandidateVisuals(self):
        from ...touchpad.stroke.stroke_glyphs import candidate_glyph, glyph_library
        matches = {item['char']: item for item in self._candidate_matches}
        visuals = []
        for character in self.strokeCandidates:
            match = matches.get(character, {})
            if match.get('match_type') == 'whole_character':
                # Repair indices belong to the expected order, not the observed count.
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
                visual['guess'] = True  # Page-filling guesses are drawn lighter.
                visuals.append(visual)
                continue
            if match.get('match_type') == 'component':
                fuzzy = tuple(index for index, ink in enumerate(self._stroke_evidence)
                              if self._fuzzy_matching and
                              (ink['ambiguous'] or index in match['corrections']))
                visuals.append(candidate_glyph(character, match['code'], len(self._code), fuzzy,
                    stroke_indices=tuple(match['observed_to_expected'])))
                continue
            # Some strict/manual matches store only the entered prefix. Recover
            # a complete accepted code; never use another candidate's corrections.
            joined = match.get('match_type') == 'joined_stroke'
            prefix = match.get('logical_prefix', match.get('code', self._code))
            variants = [row for row in self._stroke_dictionary().by_character.get(character, [])
                        if row[1].startswith(prefix)] if prefix else []
            canonical = glyph_library().get(character, {}).get('code', '')
            variants.sort(key=lambda row: (row[1] != canonical, row[1] != prefix,
                                          -(row[2] & 1), len(row[1]), row[1]))
            variant = variants[0][1] if variants else prefix
            if joined:
                mapping = match.get('observed_to_expected')
                visual = candidate_glyph(character, match['code'] if mapping is not None else variant,
                    match['logical_entered'], tuple(match['glyph_fuzzy']),
                    corrections=tuple(match.get('far_corrections', ())),
                    stroke_indices=tuple(mapping) if mapping is not None else None)
                visuals.append(dict(visual, status=match['joined_label'] + ' · ' + visual['status']))
                continue
            corrected = set(match.get('corrections', ()))
            fuzzy = tuple(index for index, evidence in enumerate(self._stroke_evidence)
                          if self._fuzzy_matching and (evidence['ambiguous'] or index in corrected))
            visuals.append(candidate_glyph(character, variant, len(self._code), fuzzy, self._prediction_mode,
                # Far strokes are also listed as replacements; show each stroke once.
                corrections=tuple(sorted({*match.get('replacement_corrections', ()), *match.get('far_corrections', ())}))
                if self._fuzzy_matching else ()))
        return visuals

    @Property(bool, notify=changed)
    def predictionMode(self): return self._prediction_mode

    @Slot(str)
    def setStrokeContext(self, text):
        if not self._local_stroke_input:
            return
        text = text[-32:]
        if text != self._stroke_context:
            self._stroke_context = text
            if not self._pending_stroke_commit and (not self._code or self._fuzzy_matching):
                self._refresh_candidates()

    @Property(int, notify=changed)
    def candidatePage(self): return self._page

    @Property(bool, notify=changed)
    def moreCandidates(self): return (self._page + 1) * self.candidatePageSize < len(self._candidates)

    @Property(int, notify=changed)
    def candidatePageCount(self): return max(1, -(-len(self._candidates) // self.candidatePageSize))

    @Property(float, constant=True)
    def selectionDistance(self): return float(TWO_FINGER_SELECTION['selectDistance'])

    @Property(int, notify=changed)
    def selectedCandidate(self): return self._selected

    @Property(str, notify=changed)
    def lastStroke(self): return self._last_stroke

    @Property("QVariantMap", constant=True)
    def strokeTrailStyle(self): return dict(TRAIL_STYLE)

    @Property(str, notify=changed)
    def commitMessage(self): return self._commit_message

    @Slot(bool)
    def setLocalStrokeInput(self, enabled):
        enabled = bool(enabled)
        if self.strokeSession.engaged and not self.strokeSession.local:
            return  # Opening the trial page must not redirect an external session.
        if enabled == self._local_stroke_input:
            return
        if self.strokeSession.engaged and self.strokeSession.local and not enabled:
            self.strokeSession.cancel("已离开试写框，笔画输入已退出")
        self._clear_external_composition()
        self._local_stroke_input = enabled
        self._stroke_context = ""
        self._code = ""
        self._clear_stroke_evidence()
        self._pending_stroke_commit = ""
        self._refresh_candidates()

    @Property(bool, notify=changed)
    def blocksVoice(self): return self._voice_gate

    def _speech_busy(self):
        return self.owner._ring_gestures.speech_busy()

    def _sync_voice_gate(self):
        stroke_blocked = (self.strokeSession.blocks_voice or (self._mode == "stroke" and
                   (self.active or self.busy or bool(self._code) or bool(self._pending_stroke_commit))))
        stroke_changed = stroke_blocked != self._stroke_gate
        self._stroke_gate = stroke_blocked
        self.owner._inline_input.stroke_mode = stroke_blocked
        # Pointer control does not own text input. Only stroke composition
        # reserves the shared IME client until its text has been settled.
        blocked = stroke_blocked
        if blocked == self._voice_gate:
            if stroke_changed:
                self.owner._ring_gestures.input_owner_changed()
            return
        self._voice_gate = blocked
        if blocked:
            self._voice_was_enabled = (self.owner._recognition_event.is_set()
                                       or self.owner._interaction_recognition_suspended)
            self.owner._recognition_event.clear()
            self.owner._cancel_utterance_event.set()
            self.owner._ignore_asr_updates_until_next_start = True
            self.owner._append_log("笔画输入期间已暂停语音识别")
        else:
            if (self.owner.recognitionEnabled
                    and not self._closed and not self.owner._quitting
                    and self.owner.connected and not self.owner._disconnect_event.is_set()
                    and not self.owner._proximity.gestures_blocked
                    and not self.owner._interaction_recognition_suspended):
                self.owner._recognition_event.set()
            self._voice_was_enabled = False
        self.owner._ring_gestures.input_owner_changed()

    @Slot()
    def _invalidate_strokes(self):
        self._selection_state = {}
        self.selectionChanged.emit()
        if self.strokeSession.phase == "running" and not self.strokeSession.local:
            self.strokeSession.cancel("输入框已改变，笔画输入已退出")
        self._code = self._pending_stroke_commit = ""
        self._clear_stroke_evidence()
        if not self._local_stroke_input:
            self._stroke_context = ""
        self._candidates = []
        self._selected = self._page = 0
        self._prediction_mode = False
        self.strokeTraceChanged.emit([], False)
        self._sync_voice_gate()
        self.changed.emit()

    def consume_gesture(self, event, connection):
        # Called on the BLE worker before voice/app mappings; never manipulate Qt here.
        if connection is not self.owner._disconnect_event or connection.is_set():
            return False
        run = self._run
        if (run is not None and connection is self._connection
                and time.monotonic() < run.switch_gesture_until
                and getattr(event, 'name', '') in {'tap', 'double-tap', 'index-pinch', 'double-index-pinch'}):
            return True
        if (self._run and self._run.switch_pending is self._run.output
                and getattr(event, "name", "") in self.strokeSession.gestures):
            return True
        if self.strokeSession.consume(event, connection):
            return True
        run = self._run
        if (not self._stroke_gate or run is None or run.mode != "stroke"
                or connection is not self._connection or connection.is_set()):
            return False
        if getattr(event, "name", "") not in self.strokeSession.gestures:
            return False
        if self._state == "running":
            self._post(run, "gesture", (run.output, event))
        return True

    @Property(float, notify=changed)
    def gain(self): return self._gain

    @gain.setter
    def gain(self, value):
        if math.isfinite(value) and .25 <= value <= 3.:
            self._set_pointer_feature('_gain', 'gain', round(value, 2))

    @Property(bool, notify=changed)
    def clicks(self): return self._clicks

    @clicks.setter
    def clicks(self, value):
        self._set_pointer_feature('_clicks', 'clicks', bool(value))

    def _set_pointer_feature(self, attr, key, value):
        setattr(self, attr, value)
        self.owner._settings.setValue('touchpad/' + key, value)
        if self._run and self._run.mode == 'pointer':
            configure = getattr(self._run.output, 'configure', None)
            if configure:
                configure(clicks=self._clicks, double_click=self._double_click,
                          right_click=self._right_click, scroll=self._scroll, scroll_gain=self._scroll_gain,
                          invert_scroll=self._invert_scroll, horizontal_swipe=self._horizontal_swipe,
                          gain=self._gain, invert_y=self._invert_y,
                          dwell_drag=self._dwell_drag, dwell_ms=self._dwell_ms)
        self.changed.emit()

    @Property(bool, notify=changed)
    def doubleClick(self): return self._double_click

    @doubleClick.setter
    def doubleClick(self, value): self._set_pointer_feature('_double_click', 'doubleClick', bool(value))

    @Property(bool, notify=changed)
    def dwellDrag(self): return self._dwell_drag

    @dwellDrag.setter
    def dwellDrag(self, value): self._set_pointer_feature('_dwell_drag', 'dwellDrag', bool(value))

    @Property(int, notify=changed)
    def dwellMs(self): return self._dwell_ms

    @dwellMs.setter
    def dwellMs(self, value):
        if math.isfinite(value) and 300 <= value <= 600:
            self._set_pointer_feature('_dwell_ms', 'dwellMs', round(value))

    @Property(bool, notify=changed)
    def rightClick(self): return self._right_click

    @rightClick.setter
    def rightClick(self, value): self._set_pointer_feature('_right_click', 'rightClick', bool(value))

    @Property(bool, notify=changed)
    def scroll(self): return self._scroll

    @scroll.setter
    def scroll(self, value): self._set_pointer_feature('_scroll', 'scroll', bool(value))

    @Property(float, notify=changed)
    def scrollGain(self): return self._scroll_gain

    @scrollGain.setter
    def scrollGain(self, value):
        if math.isfinite(value) and .25 <= value <= 3:
            self._set_pointer_feature('_scroll_gain', 'scrollGain', round(value, 2))

    @Property(bool, notify=changed)
    def invertScroll(self): return self._invert_scroll

    @invertScroll.setter
    def invertScroll(self, value): self._set_pointer_feature('_invert_scroll', 'invertScroll', bool(value))

    @Property(bool, notify=changed)
    def horizontalSwipe(self): return self._horizontal_swipe

    @horizontalSwipe.setter
    def horizontalSwipe(self, value): self._set_pointer_feature('_horizontal_swipe', 'horizontalSwipe', bool(value))

    @Property(bool, notify=changed)
    def invertY(self): return self._invert_y

    @invertY.setter
    def invertY(self, value):
        self._set_pointer_feature('_invert_y', 'invertY', bool(value))

    @Slot(object, object)
    def attach(self, source, connection):
        if self._closed or connection is not self.owner._disconnect_event or connection.is_set():
            return
        self._source, self._connection = source, connection
        self.changed.emit()

    def _connection_changed(self):
        if not self.owner.connected:
            self.stop("设备已断开")
            self._source = self._connection = None
        self.changed.emit()

    @Slot()
    def toggle(self):
        if self.active:
            self.owner._remember_input_setting("touchpad/enabled", False)
            self.stop()
        elif not self.busy and self.available:
            self.owner._remember_input_setting("touchpad/enabled", True)
            self.setInputMode("pointer")
            self.start()

    @Slot(str)
    def setInputMode(self, mode):
        if mode not in {"pointer", "stroke"} or mode == self._mode or self.busy:
            return
        if self._pending_stroke_commit or (mode == "stroke" and self._speech_busy()):
            self._message = "请先完成或取消当前输入／改写，再切换模式。"
            self._commit_message = self._message
            self.changed.emit()
            return
        self._clear_external_composition()
        self._mode = mode
        self._code = self._pending_stroke_commit = self._commit_message = ""
        self._clear_stroke_evidence()
        self._candidates = []
        self._selected = self._page = 0
        self._prediction_mode = False
        self.strokeTraceChanged.emit([], False)
        run = self._run
        if run is not None and self._state == "running":
            run.output.stop()
            run.output_ready = False
            run.mode = mode
            run.output = self._new_output(run, "mode_ready")
            self._sync_voice_gate()
            run.output.start()
        else:
            self._sync_voice_gate()
        self._message = ("用 Ring 书写笔画，轻触确认候选字。" if mode == "stroke"
                         else "用 Ring 移动指针，点击「停止触摸板」结束。")
        self.changed.emit()

    def restore_pointer(self):
        if self._mode != "pointer":
            self.setInputMode("pointer")
        elif self._run and self._state == "running":
            # Cancel a pending native focus click before resuming movement.
            run = self._run
            run.output.stop()
            run.output_ready = False
            run.output = self._new_output(run, "mode_ready")
            run.output.start()

    @Slot()
    def toggleLocalStrokeTrial(self):
        if self.busy or not self._local_stroke_input or not self._local_stroke_focus:
            return
        if self.active and self._mode == "stroke":
            self.stop("已停止笔画试写")
            return
        if self._run is None:
            self.setInputMode("pointer")
            self.start()
            if self._run is not None:
                self._run.local_trial_requested = True
        elif self._state == "running":
            self.strokeSession.request_local()

    @Slot(str)
    def addStroke(self, category):
        if category not in SYMBOLS or self._pending_stroke_commit: return
        if self._mode != "stroke" and (self.active or not self._local_stroke_input): return
        if self._speech_busy():
            self._commit_message = "请先完成或取消当前语音输入／改写，再输入笔画。"
            self.changed.emit()
            return
        self._trial_event('manual_stroke', category=category, stroke_index=len(self._code))
        self._append_stroke(category, NAMES[category])

    @Slot()
    def undoStroke(self):
        if self._pending_stroke_commit: return
        if self._code:
            self._trial_event('undo_stroke', code=self._code)
            self._code = self._code[:-1]
            self._stroke_evidence = self._stroke_evidence[:len(self._code)]
            self.strokeTraceChanged.emit([], False)
            self._refresh_candidates()

    @Slot()
    def deleteOne(self):
        """Delete one pending stroke; otherwise send the editor's Backspace."""
        if self._pending_stroke_commit or not self.strokeSession.allow_input(): return
        if self._code:
            self.undoStroke()
        else:
            self._delete_editor()

    def clearStrokesOrDeleteBefore(self):
        """A long left pull clears pending ink, or the editor's prefix when empty."""
        if self._pending_stroke_commit or not self.strokeSession.allow_input(): return
        if self._code:
            self.clearStrokes()
        else:
            self._delete_editor(to_start=True)

    def _delete_editor(self, *, to_start=False):
        if self._local_stroke_input:
            self._trial_event('delete_before' if to_start else 'delete_character')
            (self.localDeleteBeforeRequested if to_start else self.localBackspaceRequested).emit()
        elif self.strokeSession.phase == 'running':
            action = '删除前文' if to_start else '退格'
            try:
                if not self.owner._inline_input.stroke_backspace(self.strokeSession._origin, to_start=to_start):
                    self._commit_message = '输入焦点已变化，未执行' + action
                else:
                    self._stroke_context = ''
                    self._refresh_candidates()
                    self._commit_message = '已' + action
            except Exception as exc:
                self._commit_message = action + '失败：' + str(exc)
            self.changed.emit()

    @Slot()
    def clearStrokes(self):
        if self._pending_stroke_commit: return
        if self._code:
            self._trial_event('clear_strokes', code=self._code)
        self._code = ""
        self._clear_stroke_evidence()
        self.strokeTraceChanged.emit([], False)
        self._refresh_candidates()

    @Slot()
    def nextCandidates(self):
        if self._pending_stroke_commit: return
        if self.moreCandidates or (self._local_stroke_input and self._page):
            # The local menu's next page wraps to the first, as in the standalone trial.
            self._page = self._page + 1 if self.moreCandidates else 0
            self._selected = 0
            self._sync_external_composition()
            self.changed.emit()

    @Slot()
    def previousCandidates(self):
        if self._pending_stroke_commit: return
        if self._page:
            self._page -= 1
            self._selected = 0
            self._sync_external_composition()
            self.changed.emit()

    def _move_candidate(self, direction):
        if not self._candidates:
            return
        absolute = self._page * self.candidatePageSize + self._selected + direction
        absolute = max(0, min(len(self._candidates) - 1, absolute))
        self._page, self._selected = divmod(absolute, self.candidatePageSize)
        self._sync_external_composition()
        self.changed.emit()

    @Slot(int)
    def selectCandidate(self, index):
        if not self.strokeSession.allow_input():
            return
        self._select_candidate(index)

    def _select_candidate(self, index, *, suffix=''):
        if self._pending_stroke_commit:
            return
        if self._speech_busy():
            self._commit_message = "请先完成或取消当前语音输入／改写，再选择候选字。"
            self.changed.emit()
            return
        absolute = self._page * self.candidatePageSize + index
        trial = self._active_trial() if self._local_stroke_input else None
        before, code = self._personal_text, self._code
        if trial is not None and 0 <= index < self.candidatePageSize and absolute >= len(self._candidates):
            trial.commit(len(before), None, False, source='gesture', code=code)
        if 0 <= index < self.candidatePageSize and absolute < len(self._candidates):
            char = self._candidates[absolute]
            text = char + suffix
            evidence = list(self._stroke_evidence)
            match = next((row for row in self._candidate_matches if row['char'] == char), {})
            # A repaired or page-filling guess says nothing about how the user writes.
            repair = match.get('match_type') in REPAIRS
            joined = match.get('match_type') == 'joined_stroke'
            skip_learning = repair or joined
            try:
                inserted = self._commit_character(text)
            except Exception as exc:
                self._commit_message = "输入失败：" + str(exc)
                self.changed.emit()
                return
            if trial is not None:
                delivered = self._personal_text == before + text
                trial.commit(len(before), text, delivered, code=code)
                if repair:
                    trial.event('candidate_fill_commit' if match['match_type'] == 'tolerant' else 'whole_correction_commit',
                                character=char, code=code, corrected_code=match.get('code', ''),
                                match_type=match['match_type'], edits=match.get('edits', []),
                                correction_summary=match.get('correction_summary', ''),
                                delivered=delivered)
                if joined:
                    trial.event('joined_stroke_commit', character=char, physical_code=code,
                                logical_prefix=match['logical_prefix'], contacts=match['joined_contacts'],
                                groups=match['joined_groups'], delivered=delivered)
            if inserted is None:
                self._skip_commit_learning = skip_learning
                self._commit_message = "正在输入「" + char + "」…"
                self.changed.emit()
            elif inserted:
                self._tap_guard.committed()
                if not skip_learning:
                    self._learn_writer(char, evidence)
                self._commit_message = "已输入「" + char + "」"
                self._code = ""
                self._clear_stroke_evidence()
                self.strokeTraceChanged.emit([], False)
                self._refresh_candidates()
                self._check_trial_complete()
            else:
                self._commit_message = "请将光标放入目标文本框后轻触确认；候选字已保留。"
                self.changed.emit()

    def _commit_character(self, char):
        # Keep the composition until the target accepts a direct insertion.
        if self._local_stroke_input:
            self.localCharacterCommitted.emit(char)
            return True
        if sys.platform == "darwin":
            if self.owner._inline_input.stroke_commit(char):
                self._pending_stroke_commit = char
                return None
            return False
        return False

    @Slot(str, bool, str)
    def _stroke_committed(self, char, success, error):
        if char != self._pending_stroke_commit:
            return
        self._pending_stroke_commit = ""
        skip_learning, self._skip_commit_learning = self._skip_commit_learning, False
        if success:
            self._tap_guard.committed()
            candidate = char.removesuffix(' ')
            if candidate and candidate not in PUNCTUATION and not skip_learning:
                self._learn_writer(candidate, self._stroke_evidence)
            self._code = ""
            self._clear_stroke_evidence()
            context = self.owner._inline_input.stroke_context
            self._stroke_context = (context if context is not None else self._stroke_context + char)[-4:]
            self.strokeTraceChanged.emit([], False)
            self._commit_message = "已输入「" + char + "」"
            self._refresh_candidates()
        else:
            self._commit_message = "输入失败：" + (error or "目标文本框未接受文字")
            self.changed.emit()

    def _clear_external_composition(self):
        if (sys.platform == "darwin" and not self._local_stroke_input
                and (self._code or self._pending_stroke_commit or self._prediction_mode)):
            self.owner._inline_input.stroke_clear()

    def _sync_external_composition(self):
        if self._mode != "stroke" or self._local_stroke_input:
            return
        if sys.platform == "darwin":
            if self._code or self._prediction_mode:
                if not self.owner._inline_input.stroke_update(
                        self.strokeCode, self.strokeCandidates, self._selected, prediction=self._prediction_mode,
                        visuals=self.strokeCandidateVisuals):
                    self._commit_message = "请先在目标文本框选中 ProxiMic 输入法，再用 Ring 书写。"
            else:
                self.owner._inline_input.stroke_clear()

    def _sync_external_selection(self):
        if self._mode != "stroke" or self._local_stroke_input or sys.platform != "darwin":
            return
        self.owner._inline_input.stroke_selection(self._selection_state, TWO_FINGER_SELECTION['selectDistance'])

    def _stroke_dictionary(self):
        if self._dictionary is None:
            self._dictionary = StrokeDictionary(recognizer='dtw',
                personal_model=self._writer_model() if self._personal_enabled else None)
            # Every candidate is also scored by its strokes' length and direction.
            self._dictionary.stroke_vectors = StrokeVectorModel(scope='prefix')
        return self._dictionary

    @cached_property
    def _joined_model(self):
        try:
            from ...touchpad.stroke.stroke_joined import JoinedStrokeModel
            return JoinedStrokeModel.load()
        except (OSError, EOFError, ValueError, KeyError, TypeError) as error:
            self.owner._event_log('STROKE_JOINED_MODEL_UNAVAILABLE', _live_message='', error=str(error))
            return None

    def _context_model(self):
        if self._predictor is None:
            from ...touchpad.stroke.stroke_context import StrokeContext
            self._predictor = StrokeContext(model='wanxiang')
        return self._predictor

    def _can_apply_correction(self):
        # Aiming at a candidate, paging or a pending commit freezes positions.
        return (not self._closed and not self._pending_stroke_commit and self._page == 0
                and self._selection_state.get('phase', 'idle') == 'idle')

    def _refresh_candidates(self):
        """Rank ordinary and joined strokes, then offer repair and page-filling guesses."""
        try:
            self._prediction_mode = False
            self._candidate_matches = []
            dictionary = repairs = None
            if self._code:
                if len(self._stroke_evidence) != len(self._code):
                    self._stroke_evidence = [stroke_evidence(letter) for letter in self._code]
                dictionary, allowed = self._stroke_dictionary(), common_characters()
                if self._fuzzy_matching:
                    self._candidate_matches = dictionary.fuzzy_candidates(
                        self._stroke_evidence, limit=30, predictor=self._context_model(), context=self._stroke_context,
                        policy=self._fuzzy_policy, allowed_characters=allowed)
                else:
                    self._candidate_matches = dictionary.encoding_candidates(
                        self._stroke_evidence, limit=30, fuzzy=False, allowed_characters=allowed)
                self._candidates = [item['char'] for item in self._candidate_matches]
                if any(stroke.get('joined_options') for stroke in self._stroke_evidence):
                    from ...touchpad.stroke.stroke_joined import merge_joined_candidates
                    self._candidates, self._candidate_matches = merge_joined_candidates(
                        dictionary, self._stroke_evidence, self._candidates, self._candidate_matches,
                        self._joined_model, fuzzy=self._fuzzy_matching, context=self._stroke_context,
                        predictor=self._context_model() if self._fuzzy_matching else None,
                        policy=self._fuzzy_policy, allowed=allowed)
                repairs = self._correction.update(enabled=True, code=self._code, evidence=self._stroke_evidence,
                    context=self._stroke_context, dictionary=dictionary, predictor=self._context_model(),
                    allowed=allowed, normal=self._candidates)
                if repairs:
                    # A promoted complete hypothesis already supplies one recovery choice.
                    reserved = 1 if any(row.get('context_extra_slot') for row in self._candidate_matches) else 2
                    self._candidates = merge_candidates(self._candidates, repairs, reserved=reserved)
                    self._candidate_matches = self._candidate_matches + repairs
                if self._fuzzy_matching:
                    self._candidates, self._candidate_matches = fill_page(
                        self._candidates, self._candidate_matches, index=index_for(dictionary, allowed),
                        evidence=self._stroke_evidence, predictor=self._context_model(), context=self._stroke_context,
                        allowed=allowed, priors=dictionary._fuzzy_priors, page_size=PAGE_SIZE)
            elif self._stroke_context and (self._local_stroke_input or self.strokeSession.phase == "running"):
                self._candidates = self._context_model().suggestions(self._stroke_context, allowed_characters=common_characters())
                self._prediction_mode = bool(self._candidates)
            else:
                self._candidates = []
            if dictionary is None:  # Drop any repair still pending for removed strokes.
                self._correction.update(enabled=False, code='', evidence=(), context='', dictionary=None,
                                        predictor=None, allowed=None, normal=())
        except Exception as exc:
            self._candidates = []
            self._commit_message = "笔画字库加载失败：" + str(exc)
        self._selected = 0
        self._page = 0
        self._sync_external_composition()
        self._sync_voice_gate()
        self.changed.emit()

    def _append_stroke(self, category, shape, result=None, kinematics=None, points=None):
        self._code += category
        self._stroke_evidence.append(stroke_evidence(category, result, policy=self._fuzzy_policy,
                                                    metrics=kinematics))
        ink = capture_ink(points) if result and points is not None else None
        if ink is not None:
            self._stroke_evidence[-1]['ink'] = ink
        vector = stroke_vector(points) if result and points is not None else None
        if vector is not None:
            self._stroke_evidence[-1]['vector'] = vector
        self._last_stroke = shape
        self._commit_message = ""
        self._refresh_candidates()

    def _new_output(self, run, ready_kind):
        output = None
        def ended(reason, error):
            self._post(run, "output_end", (output, reason, error))
        connection = self._connection
        def gesture_detected(name):
            if run is self._run and output is run.output:
                if self._capture_target is not None: return
                run.diagnostics.note('resolved_' + name)
                self.owner._gesture_trigger.submit(name, connection)
        common = dict(connection=self._connection,
                      on_ready=lambda: self._post(run, ready_kind, output), on_end=ended,
                      on_gesture=gesture_detected, on_diagnostic=run.diagnostics.note)
        pointer_after_step = run.last_move_step
        def double_click(target=None):
            if run is not self._run or output is not run.output:
                return
            run.switch_pending = output
            if run.protocol == 'raw2':
                # Firmware pinches wait for pairing and can arrive after returning to the mouse.
                run.switch_gesture_until = time.monotonic() + SINGLE_INDEX_WAIT_S
            self._post(run, "double_click", (output, target, time.monotonic()))
        if run.mode == "stroke":
            local = self._local_stroke_input
            output = self.stroke_output_factory(**common,
                on_stroke=lambda points: self._post(run, "stroke", (output, points)),
                on_stroke_details=lambda value: self._post(run, "stroke", (output, value)),
                on_tap=lambda: None,  # Complete single-finger contacts own the configured action.
                on_double_click=double_click,
                on_two_finger_tap=lambda: self._post(run, 'two_finger_tap', (output, None)),
                raw2=run.protocol == 'raw2',
                contact_classifier=trial_contact,
                two_finger_classifier=two_finger_selection,
                on_tap_contact=lambda value: self._post(run, 'contact_tap', (output, value)),
                on_selection=lambda value: self._post(run, 'selection', (output, value)),
                on_selection_state=lambda value: self._post(run, 'selection_state', (output, value)),
                on_trace=lambda points, finished: self._post(run, "trace", (output, (points, finished))))
            if local:
                output.input_since = lambda: self._local_input_since
        else:
            selector = self.owner._ring_gestures.windowSelector
            def click_context():
                if self._capture_target: return self._capture_target.context
                epoch, phase = selector.pointer_context
                # A quick enter/cancel must also break double-click pairing,
                # even if the user never tapped inside the capture canvas.
                return ((epoch, self._capture_epoch), phase)
            pointer_options = {}
            if sys.platform == 'win32':
                from ring_python_sdk.touchpad.windows import WindowsSystemMouse
                pointer_options['mouse_factory'] = lambda: WindowsSystemMouse(
                    stroke_target=lambda: dict(local=True) if self._local_stroke_focus else None)
            output = self.output_factory(**common, **pointer_options, gain=self._gain, clicks=self._clicks,
                                         invert_y=self._invert_y, on_double_click=double_click,
                                         scroll=self._scroll, double_click=self._double_click,
                                         right_click=self._right_click, scroll_gain=self._scroll_gain,
                                         invert_scroll=self._invert_scroll, horizontal_swipe=self._horizontal_swipe,
                                         dwell_drag=self._dwell_drag, dwell_ms=self._dwell_ms,
                                         raw2=run.protocol == 'raw2',
                                         click_context=click_context,
                                         on_capture_click=self._capture_click)
            output.pointer_after_step = pointer_after_step
        return output

    def _capture_click(self, context):
        target = self._capture_target
        if target is not None: target.pointer_click(context)

    @Slot()
    def start(self):
        if not self.available or self._run is not None:
            return
        self._automatic_restart_blocked = False
        if self._mode == "stroke" and self._speech_busy():
            self._message = "请先完成或取消当前语音输入／改写，再开启笔画输入。"
            self._commit_message = self._message
            self.changed.emit()
            return
        run = SimpleNamespace(source=self._source, output=None, reason="", sdk_started=False,
                              output_ready=False, switch_pending=None, mode=self._mode,
                              switch_gesture_until=0.,
                              last_move_step=0,
                              diagnostics=TouchpadDiagnostics())
        run.protocol = 'raw2' if self.raw2Supported else 'legacy'
        self._run = run
        self._state, self._warm = "starting", False
        self._message = "正在准备笔画输入…" if run.mode == "stroke" else "正在准备鼠标控制…"
        run.output = self._new_output(run, "ready")
        self._sync_voice_gate()
        self.changed.emit()
        if run.protocol == 'raw2':
            self._prepare_raw2_heap()
        run.output.start()

    def _prepare_raw2_heap(self):
        """Do GIL-heavy work before Raw2 streams, not during it.

        Raw2 fails the stream once a token waits 200 ms. Loading the stroke
        dictionary/context lazily on the first stroke (~0.9 s on the GUI
        thread) or a full GC over them (~80-200 ms, every thread paused)
        exceeds that, so load them now and move them out of collection.
        """
        try:
            self._context_model()
            index_for(self._stroke_dictionary(), common_characters())
            self._joined_model
        except Exception:
            pass  # _refresh_candidates reports load failures where they matter.
        gc.collect()
        gc.freeze()

    def _post(self, run, kind, value):
        try:
            self._event.emit(run, kind, value)
        except RuntimeError:
            pass  # QObject already destroyed during application shutdown.

    def _watch(self, run, future, kind):
        def done(result):
            try:
                result.result()
                error = None
            except BaseException as exc:
                error = exc
            self._post(run, kind, error)
        future.add_done_callback(done)

    @Slot()
    def stop(self, reason=""):
        if self.strokeSession.engaged:
            self.strokeSession.cancel(reason, stop_output=False)
        run = self._run
        if run is None or self._state == "stopping": return
        run.reason = reason or ("已停止笔画输入" if run.mode == "stroke" else "已停止鼠标控制")
        self._clear_external_composition()
        self._code = self._pending_stroke_commit = ""
        self._clear_stroke_evidence()
        self._candidates = []
        self.strokeTraceChanged.emit([], False)
        run.output.stop()  # Synchronous gate; never wait for Quartz or MNN here.
        self._state, self._message = "stopping", "正在停止鼠标控制…"
        self.changed.emit()
        if not run.sdk_started:
            self._finish(run, None)
            return
        try:
            self._watch(run, run.source.stop_touchpad(), "stopped")
        except Exception as exc:
            self._finish(run, exc)

    def _finish(self, run, error):
        if run is not self._run: return
        run.output.stop()
        self._run = None
        self._mode = "pointer"
        error = error or getattr(run, "output_error", None)
        if isinstance(error, InterruptedError):
            run.reason, error = str(error), None
        if error and run.protocol == 'raw2' and getattr(error, 'reason', None) in {
                'input_timeout', 'inference_timeout', 'model_timeout', 'stream_failed',
                'stop_unconfirmed', 'timeout', 'device_error'}:
            # Preserve the preference, but a failed device stream must not
            # immediately fail again when the user reconnects to recover gestures.
            self._automatic_restart_blocked = True
        self._state = "error" if error else "idle"
        if isinstance(error, (PermissionError, MacPermissionError)):
            if isinstance(error, MacPermissionError) and error.state.accessibility is True:
                detail = "辅助功能已开启，但鼠标事件发送权限尚未生效。"
            else:
                detail = "当前鼠标控制进程的辅助功能权限尚未生效。"
            self._message = detail + self.owner._inline_input.permissions.instructions
        else:
            self._message = str(error) if error else run.reason or "已停止，可再次开启。"
        self._clear_external_composition()
        self._invalidate_strokes()

    @Slot(object, str, object)
    def _receive(self, run, kind, value):
        if run is not self._run: return
        if (self._state == "stopping" and kind in {"started", "ended"}
                and getattr(value, "reason", None) == "cancelled"):
            return  # The requested STOP reports its own completion or failure.
        if kind == "ready" and self._state == "starting" and value is run.output:
            try:
                run.sdk_started = True
                def ended(error):
                    run.output.stop()
                    self._post(run, "ended", error)
                def observed(event):
                    if run is not self._run:
                        discarded(event, 'touchpad_session_changed')
                        return
                    run.diagnostics.observe(event)
                    if event.kind == 'move':
                        run.last_move_step = event.step
                    elif event.kind == 'contact' and event.state == 'reset' and event.step == 0:
                        run.last_move_step = 0
                        run.output.pointer_after_step = 0
                    if (run.mode == 'stroke' or (event.kind != 'move' and
                            (run.protocol == 'legacy' or (event.kind == 'contact' and event.state == 'reset')))):
                        run.output.submit(event)
                def discarded(event, reason):
                    if event.kind == 'click':
                        ClickTrace(event, mode=run.mode).finish(reason, sdk_limit_ms=100)
                        run.diagnostics.note(reason)
                def pointer_moved(event):
                    if run is self._run and run.mode == 'stroke':
                        # Raw2 contact classification supplies immediate two-finger feedback.
                        submit = getattr(run.output, 'submit_pointer', None)
                        if submit is not None:
                            submit(event)
                    elif run is self._run and run.mode == 'pointer':
                        output = run.output
                        if event.kind in {'click', 'pointer_contact'} or event.step > output.pointer_after_step:
                            run.diagnostics.note('pointer_frame')
                            output.submit(event)
                def stats(value):
                    self._post(run, 'stats', (time.monotonic(), value, run.diagnostics.snapshot()))
                self._watch(run, run.source.start_touchpad(
                    on_event=observed,
                    on_discarded_event=discarded,
                    on_pointer_move=pointer_moved,
                    on_stats=stats,
                    on_stopped=ended,
                    protocol=run.protocol,
                    duration_s=None), "started")
            except Exception as exc:
                self._finish(run, exc)
        elif kind == "started":
            if value:
                self._finish(run, value)
            elif self._state == "starting":
                run.output.pointer_after_step = run.last_move_step
                run.output.activate()
                run.output_ready = True
                self._state = "running"
                self._message = "准备中，稍后即可书写笔画。" if run.mode == "stroke" else "准备中，稍后即可移动指针。点击「停止触摸板」结束。"
                self.changed.emit()
                if getattr(run, "local_trial_requested", False):
                    run.local_trial_requested = False
                    if self._local_stroke_input and self._local_stroke_focus:
                        self.strokeSession.request_local()
        elif kind == "mode_ready" and value is run.output and self._state == "running":
            run.output.pointer_after_step = run.last_move_step
            run.output.activate()
            run.output_ready = True
            self.changed.emit()
        elif kind == "double_click" and self._state == "running":
            output, target, created = value
            if output is not run.output: return
            self.owner._event_log("TOUCHPAD_DOUBLE_CLICK", _live_message="", mode=run.mode,
                                  age_ms=round((time.monotonic() - created) * 1000))
            tap_commit, self._tap_commit = self._tap_commit, None
            if (run.protocol != 'raw2' and self._local_stroke_input and self.strokeSession.local and tap_commit is not None
                    and created - tap_commit[0] <= TouchpadClicks.interval + .3):
                # The first tap already selected a candidate; a double click
                # means "back to the mouse", so take that character back out.
                for _ in range(tap_commit[1]):
                    self.localBackspaceRequested.emit()
            try:
                if self._capture_target is None and time.monotonic() - created <= 1:
                    self.strokeSession.request(target)
            except Exception as exc:
                self.strokeSession.fail(exc)
            finally:
                if run.switch_pending is output:
                    run.switch_pending = None
                if not self.strokeSession.engaged and output is run.output:
                    output.pointer_after_step = run.last_move_step
                    output.activate()
        elif kind in {"stroke", "trace", "gesture", "contact_tap", "two_finger_tap", "selection", "selection_state"} and self._state == "running" and run.mode == "stroke":
            output, payload = value
            if output is not run.output:
                return
            if kind in {'stroke', 'gesture', 'contact_tap', 'two_finger_tap', 'selection'} and (
                    run.switch_pending is output or not output.active or not self.strokeSession.allow_input()):
                return
            if self.strokeSession.local and self.strokeSession.engaged and self._local_input_since is None:
                return
            if kind in {'contact_tap', 'two_finger_tap', 'selection', 'selection_state'}:
                if not self.strokeSession.allow_input():
                    return
                if kind == 'two_finger_tap':
                    self._perform_tap(2)
                elif kind == 'selection_state':
                    display = payload.get('display')
                    if display:
                        # Sensor samples are tuples, which QML receives as opaque values.
                        payload = dict(payload, display=dict(origin=[float(v) for v in display['origin']],
                            points=[[float(x), float(y)] for x, y in display['points']]))
                    self._selection_state = payload
                    self.selectionChanged.emit()
                    self._sync_external_selection()
                elif kind == 'selection':
                    self._selection_state = {}
                    self._sync_external_selection()
                    decision = trial_selection(payload['decision'])
                    self._trial_event('gesture', action=decision['action'], target=decision.get('target'))
                    if decision['action'] == 'select':
                        self.selectCandidate(decision['target'])
                    elif decision['action'] == 'punctuation':
                        self.insertPunctuation(decision['target'])
                    elif decision['action'] == 'delete-one':
                        self.deleteOne()
                    elif decision['action'] == 'clear-all':
                        self.clearStrokesOrDeleteBefore()
                    if decision['action'] != 'cancel-selection' and decision.get('direction'):
                        self.selectionCommitted.emit(decision['direction'])
                    self.selectionChanged.emit()
                    self.changed.emit()
                elif self._tap_allowed(payload):
                    committed = self._tap_guard.last_commit
                    characters = 2 if self._single_finger_action == 'space' and self.strokeCandidates else 1
                    self._perform_tap(1)
                    if self._tap_guard.last_commit != committed:
                        # Remembered so the first tap of a double click can be undone.
                        self._tap_commit = (time.monotonic(), characters)
                return
            if kind == "gesture" and (run.switch_pending is output or not output.active):
                return
            if kind == "trace":
                points, finished = payload
                # Finished ink fades out in the views, as in the standalone
                # trial; the sensor's later cleanup must not cut that short.
                if points:
                    self._standard_trace = bool(finished)
                    self.strokeTraceChanged.emit(normalized_trace(points), bool(finished))
                elif not self._standard_trace:
                    self.strokeTraceChanged.emit([], False)
            elif kind == "stroke" and not self._pending_stroke_commit:
                try:
                    points = payload['points'] if isinstance(payload, dict) else payload
                    kinematics = payload.get('kinematics') if isinstance(payload, dict) else None
                    if self._local_stroke_input or self.strokeSession.phase == 'running':
                        decision = contact_gesture(points, stroke_dictionary=self._stroke_dictionary(), legacy_selection=False)
                        action = decision['action']
                        if action != 'stroke':
                            self._trial_event('gesture', action=action, target=decision.get('target'))
                        if action == 'delete-one':
                            self.deleteOne()
                            return
                        if action == 'clear-all':
                            self.clearStrokes()
                            return
                        if action != 'stroke':
                            return
                    recognition_start = time.monotonic()
                    result = self._stroke_dictionary().recognize(points)
                    if result is not None and self._joined_model is not None:
                        options = self._joined_model.recognize(points)
                        if options:
                            result = dict(result, joined_options=options)
                    trial = self._active_trial() if self._local_stroke_input else None
                    if trial is not None:
                        recognition_ms = (time.monotonic()-recognition_start)*1000
                        options = result.get('joined_options', []) if result else []
                        if options or any(stroke.get('joined_options') for stroke in self._stroke_evidence):
                            trial.event('joined_contact_unscored', recognition_ms=recognition_ms,
                                        options=options, stroke_index=len(self._code))
                        else:
                            trial.stroke(len(self._personal_text), len(self._code), result, recognition_ms)
                    if result is not None:
                        run.diagnostics.note('stroke_recognized')
                        if self.strokeSession.allow_input():
                            self._append_stroke(result["category"], result["shape"], result, kinematics, points)
                            self.owner._event_log("STROKE_RECOGNIZED", _live_message="", points=len(points),
                                kinematics=kinematics,
                                adaptation=self._stroke_evidence[-1].get('adaptation'),
                                effective_fuzzy_policy=self._stroke_evidence[-1].get('effective_policy', self._fuzzy_policy.snapshot()),
                                category=result['category'], shape=result['shape'], distance=result['distance'],
                                recognizer=result.get('recognizer', 'unknown'),
                                template_profile=result.get('template_profile', 'none'),
                                category_alternatives=result.get('category_alternatives', []),
                                category_scores=result.get('category_scores', []), fuzzy_matching=self._fuzzy_matching,
                                joined_options=result.get('joined_options', []),
                                personal_adaptation=result.get('personal_adaptation'),
                                base_category_scores=result.get('base_category_scores'),
                                fuzzy_policy=self._fuzzy_policy.snapshot(),
                                context_ranking=ranking_snapshot(),
                                fuzzy_candidates=[dict(char=item['char'], code=item.get('code', ''), cost=item.get('cost'),
                                    match_type=item.get('match_type'),
                                    context_promoted=item.get('context_promoted', False),
                                    language_bonus=item.get('language_bonus', 0.), final_score=item.get('final_score'),
                                    fold_score=item.get('fold_score', 0.),
                                    fold_matches=item.get('fold_matches', []),
                                    fold_corrections=item.get('fold_corrections', []),
                                    corrections=item.get('corrections', []),
                                    left_dot_corrections=item.get('left_dot_corrections', []))
                                    for item in self._candidate_matches[:5]],
                                recognition_and_candidates_ms=round((time.monotonic()-recognition_start)*1000, 1))
                    else:
                        run.diagnostics.note('stroke_unrecognizable')
                        self._standard_trace = False
                        self.strokeTraceChanged.emit([], False)
                except Exception as exc:
                    run.diagnostics.note('stroke_recognition_error')
                    self._commit_message = "笔画识别失败：" + str(exc)
                    self.changed.emit()
            elif kind == 'stroke':
                run.diagnostics.note('stroke_waiting_commit')
            elif kind == "gesture" and not self._pending_stroke_commit:
                name = getattr(payload, "name", "")
                if name == "swipe-right": self._move_candidate(1)
                elif name == "swipe-left": self._move_candidate(-1)
                elif name == "swipe-up": self.undoStroke()
                elif name == "swipe-down": self.clearStrokes()
                elif name == "tap" and run.protocol != 'raw2' and not self._local_stroke_input:
                    self.selectCandidate(self._selected)
        elif kind == "stats" and self._state == "running":
            captured_at, value, counts = value
            app = QGuiApplication.instance()
            foreground = app.applicationState() == Qt.ApplicationActive if app is not None else None
            report = run.diagnostics.report(value, counts, captured_at, foreground=foreground,
                                             visibility_epoch=self._visibility_epoch, now=time.monotonic())
            if report is not None:
                if run.protocol == 'raw2':
                    report['pipeline'] = 'raw2-v2'
                # Keep reason counts numeric; the generic logger truncates long string fields.
                reasons = {'count_' + key: count for key, count in report.pop('events').items()}
                self.owner._event_log('TOUCHPAD_STATS', _live_message='', mode=run.mode, **report, **reasons)
            ready = getattr(value, 'ready', None)
            warm = bool(ready) if ready is not None else value.warmup_frames >= 200
            if warm != self._warm:
                self._warm = warm
                self._message = ("用 Ring 书写笔画，轻触确认候选字。" if run.mode == "stroke" else "用 Ring 移动指针，点击「停止触摸板」结束。") if warm else "信号恢复中，正在重新准备…"
                self.changed.emit()
        elif kind in {"ended", "stopped"}:
            self._finish(run, value)
        elif kind == "output_end" and self._state != "stopping":
            output, reason, error = value
            if output is not run.output: return
            if not run.sdk_started:
                run.reason = reason
                self._finish(run, error)
            else:
                run.output_error = error
                self.stop(reason or str(error or "鼠标控制已停止"))

    def _tap_allowed(self, contact):
        decision = self._tap_guard.decide(contact)
        self._trial_event('tap_confirmation', **decision)
        return decision['allowed']

    # Sentence trial: the standalone program's input task, timed from Start.
    def _active_trial(self):
        return self._trial if self._trial is not None and self._trial.active else None

    def _trial_event(self, kind, **values):
        trial = self._active_trial() if self._local_stroke_input else None
        if trial is not None:
            trial.event(kind, **values)

    @Property(str, notify=changed)
    def trialTarget(self): return self._trial_target

    @Slot(str)
    def setTrialTarget(self, text):
        if self._active_trial() is None and text != self._trial_target:
            self._trial_target = text
            self.owner._settings.setValue('touchpad/trialTarget', text)
            self.changed.emit()

    @Property(bool, notify=changed)
    def trialActive(self): return self._active_trial() is not None

    @Property(bool, notify=changed)
    def trialFinished(self): return self._trial is not None and not self._trial.active

    @Property(str, notify=changed)
    def trialPreview(self):
        trial = self._trial
        if trial is None:
            return '自由书写中；开始实验后显示目标进度。'
        actual = self._personal_text if trial.active else trial.actual
        parts = []
        for index, char in enumerate(trial.target):
            color = ('#1a936f' if index < len(actual) and actual[index] == char
                     else '#d64550' if index < len(actual) else '#8b94a5')
            background = 'background-color:#dbe5ff;' if index == len(actual) else ''
            parts.append(f'<span style="color:{color};{background}">{html.escape(char)}</span>')
        if len(actual) > len(trial.target):
            parts.append('<span style="color:#d64550">' + html.escape(actual[len(trial.target):]) + '</span>')
        return ''.join(parts)

    @Property(int, notify=changed)
    def trialProgress(self):
        trial = self._trial
        if trial is None:
            return 0
        actual = self._personal_text if trial.active else trial.actual
        return round(sum(a == b for a, b in zip(actual, trial.target)) * 100 / len(trial.target))

    @Property(float, notify=changed)
    def trialPosition(self):
        """How far through the target the typed text is, 0..1."""
        trial = self._trial
        if trial is None:
            return 0.
        actual = self._personal_text if trial.active else trial.actual
        return min(1., len(actual) / len(trial.target))

    @Property(str, notify=changed)
    def trialHint(self):
        trial = self._active_trial()
        if trial is None:
            return self._trial_hint or TRIAL_IDLE_HINT
        actual = self._personal_text
        char = trial.target[len(actual)] if len(actual) < len(trial.target) else None
        code = trial.codes.get(char, '')
        joined = next((match for match in self._candidate_matches
                       if match['char'] == char and match.get('match_type') == 'joined_stroke'), {})
        entered = joined.get('logical_entered', len(self._code))
        expected = NAMES.get(code[entered], '—') if entered < len(code) else '请选字 / 退笔'
        unsupported = ''.join(dict.fromkeys(c for c in trial.target if c not in trial.codes and c not in PUNCTUATION))
        return (f'当前第 {len(actual)+1} 字：{char or "已超出原文"} · 下一笔：{expected} · '
                '按钮输入和存在连笔歧义的接触单独记录。' +
                (f'\n原文含未支持字符：{unsupported}。可结束保存未完成结果，或重新编辑原文。' if unsupported else ''))

    @Slot()
    def startTrial(self):
        target = ' '.join(self._trial_target.splitlines()).strip()
        if self._active_trial() is not None or self._pending_stroke_commit or not self._local_stroke_input:
            return
        if not target:
            self._trial_hint = '请先输入待测试文本。'
            self.changed.emit()
            return
        self._code = ''
        self._clear_stroke_evidence()
        self._personal_history = TextLearningHistory()  # A new task is not a correction of earlier text.
        self._tap_guard.reset()
        self.localEditorClearRequested.emit()
        self._personal_text, self._personal_cursor = '', 0
        self._stroke_context = ''
        self._trial = SentenceTrial(target, self._stroke_dictionary().entries)
        self._trial_hint = ''
        self.owner._event_log('STROKE_TRIAL', _live_message='', phase='start', characters=len(target))
        self.strokeTraceChanged.emit([], False)
        self._refresh_candidates()

    @Slot()
    def finishTrial(self): self._finish_trial('manual')

    def _check_trial_complete(self):
        trial = self._active_trial()
        if trial is not None and self._local_stroke_input and self._personal_text == trial.target:
            self._finish_trial('completed')

    def _finish_trial(self, reason):
        trial = self._active_trial()
        if trial is None:
            return
        report = trial.finish(self._personal_text)
        report.update(finish_reason=reason, source='mythlink-app', recognizer='dtw',
            fuzzy_matching=self._fuzzy_matching, fuzzy_policy=self._fuzzy_policy.snapshot(),
            adaptive_fuzzy_policy=dict(ADAPTIVE_SETTINGS), context_ranking=ranking_snapshot(),
            context_model=getattr(self._predictor, 'model', 'wanxiang'), common_only=True,
            personal_learning=self._personal_enabled, writing_profile=self._personal_name,
            joined_stroke_enabled=self._joined_model is not None,
            joined_contacts_unscored=report['counts'].get('joined_contact_unscored', 0),
            whole_correction_enabled=True, candidate_fill_enabled=self._fuzzy_matching,
            stroke_vector_enabled=True, tap_guard_enabled=True, tap_guard_policy=self._tap_guard.snapshot(),
            selection_policy=dict(TRIAL_SELECTION))
        percent = lambda value: '—' if value is None else f'{value:.1%}'
        summary = (('完成' if report['completed'] else '已结束（未完成）') +
                   f" · 用时 {report['elapsed_s']:.1f} 秒 · {report['characters_per_minute']:.1f} 字/分"
                   f" · 文本准确率 {percent(report['text_accuracy'])} · 笔画准确率 {percent(report['stroke_accuracy'])}")
        try:
            folder = Path(self.trial_report_dir or Path(QStandardPaths.writableLocation(
                QStandardPaths.StandardLocation.AppDataLocation)) / 'stroke-trials')
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / f'trial-{time.time_ns()}.json'
            write_report(path, report)
            self._trial_hint = summary + f'\n报告：{path}'
        except OSError as exc:
            self._trial_hint = summary + f'\n报告保存失败：{exc}'
        if report['contains_manual_strokes']:
            self._trial_hint += ' · 本轮含手动笔画，请勿作为纯戒指实验数据。'
        self.owner._event_log('STROKE_TRIAL', _live_message='', phase='end', reason=reason,
                              completed=report['completed'], elapsed_s=round(report['elapsed_s'], 1))
        self.changed.emit()

    def close(self):
        self._closed = True
        self._finish_trial('window_closed')
        self._correction.close()
        self.strokeSession.close()
        self.stop("鼠标控制已关闭")
        self._clear_external_composition()
        self._invalidate_strokes()
