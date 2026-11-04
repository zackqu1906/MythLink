"""GUI lifecycle for the existing Ring session; high-rate events never enter Qt."""
from __future__ import annotations

import math
import sys
import time
from types import SimpleNamespace

from PySide6.QtCore import QObject, Property, QSettings, Qt, Signal, Slot
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
from ...touchpad.stroke.stroke_fuzzy import stroke_evidence, FuzzyPolicy, retune_evidence, ADAPTIVE_SETTINGS
from ...touchpad.stroke.stroke_personal import PersonalStrokeModel, TextLearningHistory, capture_ink, profile_key, qt_text_index
from ...touchpad.stroke.stroke_context import ranking_snapshot
from ...touchpad.stroke.stroke_menu import PAGE_SIZE, PUNCTUATION_CELLS, trial_contact, trial_selection
from ...touchpad.stroke.stroke_contact_intent import CONTACT_INTENT

PUNCTUATION = tuple(symbol for _, _, _, symbol, _ in PUNCTUATION_CELLS)
from ...touchpad.stroke.stroke_test_gestures import recognize as contact_gesture
from ...touchpad.stroke.stroke_tap_guard import TapConfirmationGuard


class TouchpadController(QObject):
    changed = Signal()
    selectionChanged = Signal()
    localCharacterCommitted = Signal(str)
    localBackspaceRequested = Signal()
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
        self._invert_y = owner._settings.value("touchpad/invertY", False, type=bool)
        self._warm = False
        self.stroke_output_factory = TouchpadStrokeOutput
        self._mode = "pointer"
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
        self._fuzzy_policy = FuzzyPolicy(round(self._number('fuzzyStrength', 50, 0, 100)))
        self._candidates = []
        self._selected = self._page = 0
        self._pending_stroke_commit = ""
        self._local_stroke_input = self._prediction_mode = False
        self._stroke_context = ""
        self._stroke_gate = self._voice_was_enabled = False
        self._voice_gate = False
        self._standard_trace = False
        self._tap_commit_at = None
        self._local_stroke_focus = False
        self._local_input_since = None
        self._selection_state = {}
        self._tap_guard = TapConfirmationGuard()
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
    def available(self):
        return (self.supported and not self._closed and self.owner.connected
                and not self.owner.busy and self._source is not None
                and self._connection is self.owner._disconnect_event
                and not self._connection.is_set())

    @Property(bool, notify=changed)
    def active(self): return self._state in {"starting", "running"}

    @Property(bool, notify=changed)
    def busy(self): return self._state in {"starting", "stopping"}

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

    @Property(str, constant=True)
    def strokeGestureLabel(self): return "Touchpad 双击"

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

    @Property(list, notify=changed)
    def strokeTokens(self):
        corrected = {index for item in self._candidate_matches[self._page*self.candidatePageSize:self._page*self.candidatePageSize+self.candidatePageSize]
                     for index in item['corrections']} if self._fuzzy_matching else set()
        replacements = set(self._candidate_matches[self._page*self.candidatePageSize].get('replacement_corrections', ())) \
            if self._fuzzy_matching and self._candidate_matches[self._page*self.candidatePageSize:] else set()
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
        self._personal_text, self._personal_cursor = text, qt_text_index(text, cursor)
        if self._personal_model is not None and self._personal_history.reconcile(self._personal_model, text):
            self._save_writer()
            self.changed.emit()

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
        if symbol not in PUNCTUATION or not self.strokeSession.allow_input() or self._pending_stroke_commit:
            return
        if self._speech_busy():
            return
        if self._commit_character(symbol):
            self.clearStrokes()

    @Property(list, notify=changed)
    def strokeCandidates(self): return self._candidates[self._page * self.candidatePageSize:self._page * self.candidatePageSize + self.candidatePageSize]

    @Property(list, notify=changed)
    def strokeCandidateVisuals(self):
        from ...touchpad.stroke.stroke_glyphs import candidate_glyph, glyph_library
        matches = {item['char']: item for item in self._candidate_matches}
        visuals = []
        for character in self.strokeCandidates:
            match = matches.get(character, {})
            # Some strict/manual matches store only the entered prefix. Recover
            # a complete accepted code; never use another candidate's corrections.
            prefix = match.get('code', self._code)
            variants = [row for row in self._stroke_dictionary().by_character.get(character, [])
                        if row[1].startswith(prefix)] if prefix else []
            canonical = glyph_library().get(character, {}).get('code', '')
            variants.sort(key=lambda row: (row[1] != canonical, row[1] != prefix,
                                          -(row[2] & 1), len(row[1]), row[1]))
            variant = variants[0][1] if variants else prefix
            corrected = set(match.get('corrections', ()))
            fuzzy = tuple(index for index, evidence in enumerate(self._stroke_evidence)
                          if self._fuzzy_matching and (evidence['ambiguous'] or index in corrected))
            visuals.append(candidate_glyph(character, variant, len(self._code), fuzzy, self._prediction_mode,
                corrections=tuple(match.get('replacement_corrections', ())) if self._fuzzy_matching else ()))
        return visuals

    @Property(bool, notify=changed)
    def predictionMode(self): return self._prediction_mode

    @Slot(str)
    def setStrokeContext(self, text):
        if not self._local_stroke_input:
            return
        text = text[-4:]
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
    def selectionDistance(self): return float(CONTACT_INTENT['selectDistance'])

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
        if (self._run and self._run.switch_pending is self._run.output
                and getattr(event, "name", "") in {"tap", "swipe-left", "swipe-right", "swipe-up", "swipe-down"}):
            return True
        if self.strokeSession.consume(event, connection):
            return True
        run = self._run
        if (not self._stroke_gate or run is None or run.mode != "stroke"
                or connection is not self._connection or connection.is_set()):
            return False
        if getattr(event, "name", "") not in {"swipe-left", "swipe-right", "swipe-up", "swipe-down", "tap"}:
            return False
        if self._state == "running":
            self._post(run, "gesture", (run.output, event))
        return True

    @Property(float, notify=changed)
    def gain(self): return self._gain

    @gain.setter
    def gain(self, value):
        if not self._run and math.isfinite(value) and .25 <= value <= 3.:
            self._gain = round(value, 2)
            self.owner._settings.setValue("touchpad/gain", self._gain)
            self.changed.emit()

    @Property(bool, notify=changed)
    def clicks(self): return self._clicks

    @clicks.setter
    def clicks(self, value):
        if not self._run:
            self._clicks = bool(value)
            self.owner._settings.setValue("touchpad/clicks", self._clicks)
            self.changed.emit()

    @Property(bool, notify=changed)
    def invertY(self): return self._invert_y

    @invertY.setter
    def invertY(self, value):
        if not self._run:
            self._invert_y = bool(value)
            self.owner._settings.setValue("touchpad/invertY", self._invert_y)
            self.changed.emit()

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
        self._append_stroke(category, NAMES[category])

    @Slot()
    def undoStroke(self):
        if self._pending_stroke_commit: return
        if self._code:
            self._code = self._code[:-1]
            self._stroke_evidence = self._stroke_evidence[:len(self._code)]
            self.strokeTraceChanged.emit([], False)
            self._refresh_candidates()

    @Slot()
    def clearStrokes(self):
        if self._pending_stroke_commit: return
        self._code = ""
        self._clear_stroke_evidence()
        self.strokeTraceChanged.emit([], False)
        self._refresh_candidates()

    @Slot()
    def nextCandidates(self):
        if self._pending_stroke_commit: return
        if self.moreCandidates:
            self._page += 1
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

    def _select_candidate(self, index):
        if self._pending_stroke_commit:
            return
        if self._speech_busy():
            self._commit_message = "请先完成或取消当前语音输入／改写，再选择候选字。"
            self.changed.emit()
            return
        absolute = self._page * self.candidatePageSize + index
        if 0 <= index < self.candidatePageSize and absolute < len(self._candidates):
            char = self._candidates[absolute]
            evidence = list(self._stroke_evidence)
            try:
                inserted = self._commit_character(char)
            except Exception as exc:
                self._commit_message = "输入失败：" + str(exc)
                self.changed.emit()
                return
            if inserted is None:
                self._commit_message = "正在输入「" + char + "」…"
                self.changed.emit()
            elif inserted:
                self._tap_guard.committed()
                self._learn_writer(char, evidence)
                self._commit_message = "已输入「" + char + "」"
                self._code = ""
                self._clear_stroke_evidence()
                self.strokeTraceChanged.emit([], False)
                self._refresh_candidates()
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
        if success:
            if char not in PUNCTUATION:
                self._learn_writer(char, self._stroke_evidence)
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
        self.owner._inline_input.stroke_selection(self._selection_state, CONTACT_INTENT['selectDistance'])

    def _stroke_dictionary(self):
        if self._dictionary is None:
            self._dictionary = StrokeDictionary(recognizer='dtw',
                personal_model=self._writer_model() if self._personal_enabled else None)
        return self._dictionary

    def _refresh_candidates(self):
        try:
            self._prediction_mode = False
            self._candidate_matches = []
            if self._code:
                if len(self._stroke_evidence) != len(self._code):
                    self._stroke_evidence = [stroke_evidence(letter) for letter in self._code]
                if self._fuzzy_matching:
                    if self._predictor is None:
                        from ...touchpad.stroke.stroke_context import StrokeContext
                        self._predictor = StrokeContext()
                    self._candidate_matches = self._stroke_dictionary().fuzzy_candidates(
                        self._stroke_evidence, limit=30, predictor=self._predictor, context=self._stroke_context,
                        policy=self._fuzzy_policy, allowed_characters=common_characters())
                    self._candidates = [item['char'] for item in self._candidate_matches]
                else:
                    self._candidates = self._stroke_dictionary().candidates(self._code, limit=30,
                                                                          allowed_characters=common_characters())
            elif self._stroke_context and (self._local_stroke_input or self.strokeSession.phase == "running"):
                if self._predictor is None:
                    from ...touchpad.stroke.stroke_context import StrokeContext
                    self._predictor = StrokeContext()
                self._candidates = self._predictor.suggestions(self._stroke_context, allowed_characters=common_characters())
                self._prediction_mode = bool(self._candidates)
            else:
                self._candidates = []
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
            self._post(run, "double_click", (output, target, time.monotonic()))
        if run.mode == "stroke":
            local = self._local_stroke_input
            output = self.stroke_output_factory(**common,
                on_stroke=lambda points: self._post(run, "stroke", (output, points)),
                on_stroke_details=lambda value: self._post(run, "stroke", (output, value)),
                on_tap=lambda: None,  # Firmware tap owns confirmation; SDK click only separates ink.
                on_double_click=double_click,
                # Both the local trial and the macOS palette use hold-to-select.
                # External taps stay with firmware confirmation (on_tap above).
                contact_classifier=trial_contact,
                on_tap_contact=(lambda value: self._post(run, 'contact_tap', (output, value))) if local else None,
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
        if self._mode == "stroke" and self._speech_busy():
            self._message = "请先完成或取消当前语音输入／改写，再开启笔画输入。"
            self._commit_message = self._message
            self.changed.emit()
            return
        run = SimpleNamespace(source=self._source, output=None, reason="", sdk_started=False,
                              output_ready=False, switch_pending=None, mode=self._mode,
                              last_move_step=0,
                              diagnostics=TouchpadDiagnostics())
        self._run = run
        self._state, self._warm = "starting", False
        self._message = "正在准备笔画输入…" if run.mode == "stroke" else "正在准备鼠标控制…"
        run.output = self._new_output(run, "ready")
        self._sync_voice_gate()
        self.changed.emit()
        run.output.start()

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
                    if run.mode == 'stroke' or event.kind != 'move':
                        run.output.submit(event)
                def discarded(event, reason):
                    if event.kind == 'click':
                        ClickTrace(event, mode=run.mode).finish(reason, sdk_limit_ms=100)
                        run.diagnostics.note(reason)
                def pointer_moved(event):
                    if run is self._run and run.mode == 'pointer':
                        output = run.output
                        if event.step > output.pointer_after_step:
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
            tap_commit_at, self._tap_commit_at = self._tap_commit_at, None
            if (self._local_stroke_input and self.strokeSession.local and tap_commit_at is not None
                    and created - tap_commit_at <= TouchpadClicks.interval + .3):
                # The first tap already selected a candidate; a double click
                # means "back to the mouse", so take that character back out.
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
        elif kind in {"stroke", "trace", "gesture", "contact_tap", "selection", "selection_state"} and self._state == "running" and run.mode == "stroke":
            output, payload = value
            if output is not run.output:
                return
            if self.strokeSession.local and self.strokeSession.engaged and self._local_input_since is None:
                return
            if kind in {'contact_tap', 'selection', 'selection_state'}:
                if not self.strokeSession.allow_input() or (kind == 'contact_tap' and not self._local_stroke_input):
                    return
                if kind == 'selection_state':
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
                    if decision['action'] == 'select':
                        self.selectCandidate(decision['target'])
                    elif decision['action'] == 'punctuation':
                        self.insertPunctuation(decision['target'])
                    self.selectionChanged.emit()
                    self.changed.emit()
                elif self._tap_guard.decide(payload)['allowed']:
                    committed = self._tap_guard.last_commit
                    self.selectCandidate(0)
                    if self._tap_guard.last_commit != committed:
                        # Remembered so the first tap of a double click can be undone.
                        self._tap_commit_at = time.monotonic()
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
                    if self._local_stroke_input:
                        decision = contact_gesture(points, stroke_dictionary=self._stroke_dictionary(), legacy_selection=False)
                        action = decision['action']
                        if action == 'delete-one':
                            if self._code:
                                self.undoStroke()
                            elif self.strokeSession.allow_input():
                                self.localBackspaceRequested.emit()
                            return
                        if action == 'clear-all':
                            self.clearStrokes()
                            return
                        if action != 'stroke':
                            return
                    recognition_start = time.monotonic()
                    result = self._stroke_dictionary().recognize(points)
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
                                personal_adaptation=result.get('personal_adaptation'),
                                base_category_scores=result.get('base_category_scores'),
                                fuzzy_policy=self._fuzzy_policy.snapshot(),
                                context_ranking=ranking_snapshot(),
                                fuzzy_candidates=[dict(char=item['char'], code=item['code'], cost=item['cost'],
                                    context_promoted=item.get('context_promoted', False),
                                    language_bonus=item.get('language_bonus', 0.), final_score=item.get('final_score'),
                                    fold_score=item.get('fold_score', 0.),
                                    fold_matches=item.get('fold_matches', []),
                                    fold_corrections=item.get('fold_corrections', []),
                                    corrections=item['corrections'],
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
                elif name == "tap" and not self._local_stroke_input:
                    self.selectCandidate(self._selected)
        elif kind == "stats" and self._state == "running":
            captured_at, value, counts = value
            app = QGuiApplication.instance()
            foreground = app.applicationState() == Qt.ApplicationActive if app is not None else None
            report = run.diagnostics.report(value, counts, captured_at, foreground=foreground,
                                             visibility_epoch=self._visibility_epoch, now=time.monotonic())
            if report is not None:
                # Keep reason counts numeric; the generic logger truncates long string fields.
                reasons = {'count_' + key: count for key, count in report.pop('events').items()}
                self.owner._event_log('TOUCHPAD_STATS', _live_message='', mode=run.mode, **report, **reasons)
            warm = value.warmup_frames >= 200
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

    def close(self):
        self._closed = True
        self.strokeSession.close()
        self.stop("鼠标控制已关闭")
        self._clear_external_composition()
        self._invalidate_strokes()
