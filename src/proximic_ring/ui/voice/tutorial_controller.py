"""Observe ordinary IME transactions for lessons; never capture audio or gestures."""
from PySide6.QtCore import QObject, Property, Signal, Slot

from ...gestures.gesture_settings import GESTURE_LABELS


class VoiceTutorialController(QObject):
    changed = Signal()

    def __init__(self, inline, owner):
        super().__init__(owner)
        self.inline = inline
        self.owner = owner
        self._active = False
        self._stage = 0
        self._focused = False
        self._key = None
        self._excluded = None
        self._complete = False
        self._saw_polish = False
        self._saw_edit = False
        self._message = ""
        owner.gestureSettingsChanged.connect(self.changed)
        owner.connectedChanged.connect(self.changed)
        owner.recognitionEnabledChanged.connect(self.changed)
        owner.settingsChanged.connect(self.changed)

    def _current_key(self):
        return (self.inline._epoch, self.inline._client_id, self.inline._utterance_id)

    @Property(bool, notify=changed)
    def active(self):
        return self._active

    @Property(bool, notify=changed)
    def stepComplete(self):
        return self._complete

    @Property(str, notify=changed)
    def message(self):
        return self._message

    @Property(bool, notify=changed)
    def busy(self):
        return self._key == self._current_key() and self.inline._awaiting_settlement()

    @Slot(bool)
    def setEditorFocused(self, focused):
        self._focused = focused
        self.changed.emit()

    @Slot(result=bool)
    def start(self):
        if self.inline._awaiting_settlement():
            self._message = "请先结束当前语音输入，再开始教学。"
            self.changed.emit()
            return False
        if not self._active:
            self.inline.changed.connect(self._observe)
            self.inline.transactionSettled.connect(self._settled)
        self._active = True
        self._stage = 0
        self._key = None
        self._excluded = self._current_key()
        self._complete = self._saw_polish = self._saw_edit = False
        self._message = ""
        self.changed.emit()
        return True

    @Slot()
    def stop(self):
        if self._active:
            self.inline.changed.disconnect(self._observe)
            self.inline.transactionSettled.disconnect(self._settled)
        self._active = False
        self._key = None
        self._complete = False
        self.changed.emit()

    @Slot()
    def advance(self):
        if self._active and self._complete and not self.busy:
            self._stage += 1
            self._complete = self._saw_polish = self._saw_edit = False
            self.changed.emit()

    def _observe(self):
        if not self._active:
            return
        state = self.inline._view
        key = self._current_key()
        if self._active:
            if (self._focused and all(key) and key != self._excluded and key != self._key
                    and state.get("ready") and state.get("phase") in {"starting", "listening"}):
                self._key = key
                self._complete = self._saw_polish = self._saw_edit = False
            if self._key == key:
                self._saw_polish |= state.get("phase") == "polishing"
                self._saw_edit |= bool(state.get("edit_requested")) or state.get("phase") == "editing"
                if state.get("phase") in {"undone", "interrupted", "error"}:
                    self._complete = False
                if self._stage == 1 and state.get("phase") == "dictated" and not state.get("polished"):
                    # Restoring the pre-polish sentence also settles as dictated.
                    self._complete = False
        self.changed.emit()

    def _settled(self, key, phase, text, error, previous):
        if not self._active or previous or tuple(key[:3]) != self._key:
            return
        if error or phase in {"undone", "interrupted", "error"}:
            self._complete = False
        elif text.strip():
            self._complete = (
                (self._stage == 0 and phase == "dictated" and not self._saw_edit)
                or (self._stage == 1 and phase == "dictated" and self._saw_polish)
                or (self._stage == 2 and phase == "edited" and self._saw_edit))
        self.changed.emit()

    @Property("QVariantMap", notify=changed)
    def hint(self):
        def result(state_id, title, text, action="", function="", tone="normal"):
            gestures = (["swipe-down"] if action == "polish" else
                        [g for g in self.owner.gestureBindings.get(action, []) if g])
            label = " / ".join(GESTURE_LABELS.get(g, g) for g in gestures)
            return dict(id=state_id, title=title, tone=tone,
                        text=text.replace("{gesture}", label or "对应手势"),
                        gestures=gestures, functionAsset=function)

        if not self._active:
            return result("inactive", "准备学习", "开始教学后，这里会提示当前操作。")
        if self._message:
            return result("blocked", "暂时无法开始", self._message)
        if not self.owner.connected or not self.owner.recognitionEnabled:
            return result("connection", "完成使用准备", "请先连接 Ring，并开启语音输入。")
        if not self._focused:
            return result("focus", "点入练习框", "把光标放回左侧输入框，继续本阶段。")
        state = self.inline._view if self._key == self._current_key() else {}
        phase = state.get("phase", "idle")
        if phase == "starting":
            return result("starting", "正在开启", "稍等片刻，准备好后就可以说话。")
        if phase in {"finishing", "polishing", "editing"} or state.get("awaiting_readback"):
            action = "应用编辑" if self._saw_edit else "润色" if self._saw_polish else "定稿"
            return result("processing-" + action, "正在" + action, "正在" + action + "，请等待浮窗完成。")
        if state.get("error"):
            return result("error", "请重试本阶段", str(state["error"]))
        if self._complete:
            return result("complete-" + str(self._stage), "本阶段已完成",
                          ["听写已定稿，可以进入润色阶段。", "润色已应用，可以进入编辑阶段。", "编辑已应用，可以完成教学。"][min(self._stage, 2)], tone="success")
        if phase == "listening":
            if not str(state.get("raw") or "").strip():
                return result("speak-" + str(self._stage), "请说出修改指令" if self._stage == 2 else "请开始说话",
                              "例如：把下午三点改成下午四点。" if self._stage == 2 else "说一句你想输入的话，文字会出现在左侧。")
            if self._stage == 2:
                return result("convert", "将指令转换为编辑", "文字仍带下划线时{gesture}，转换为编辑并结束收音。", "switch_mode", "function-edit")
            if self._stage == 1:
                return result("polish-live", "下滑润色本句", "说完后{gesture}，结束收音并润色这句话。", "polish")
            return result("finalize", "确认听写结果", "文字尚未定稿。说完后{gesture}，即可定稿本句。", "end")
        if self._stage == 1 and phase == "dictated" and not state.get("polished"):
            return result("polish-committed", "下滑润色当前句", "保持光标在练习框，{gesture}润色刚才的听写。", "polish")
        if self._stage == 2:
            if self.owner.speechControlMode == "proximity":
                return result("start-edit", "开启语音编辑", "保持原句，靠近 Ring 开始新一轮语音。")
            return result("start-edit", "开启语音编辑", "保持原句，{gesture}开启语音。", "confirm")
        if self.owner.speechControlMode == "proximity":
            return result("start", "开启语音输入", "靠近 Ring 开启语音。")
        return result("start", "开启语音输入", "{gesture}开启语音。", "confirm")
