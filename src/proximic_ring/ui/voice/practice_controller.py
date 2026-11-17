"""An explicitly armed practice session; never owns an IME or history writer.

The connected runtime lends its selected PCM stream. Disconnected microphone
practice owns a separate MicrophoneSource. All worker results carry a generation
so leaving the page cannot apply a late recognition/edit to a new exercise.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import threading
import time
from typing import Callable

import numpy as np
from PySide6.QtCore import QObject, Property, QTimer, Signal, Slot

from ...asr.factory import ASRBackendSettings, asr_backend_kind, create_asr_backend, create_streaming_asr_backend
from ...audio.microphone import MicrophoneSource
from ...gestures.gesture_settings import GestureBindings
from ...text_processing.llm import OpenAICompatibleTextProcessor
from ...text_processing.model import LLMSettings


@dataclass(frozen=True)
class PracticeConfiguration:
    source: str
    microphone: str
    shared: bool
    backend: str
    asr: ASRBackendSettings
    llm: LLMSettings
    bindings: GestureBindings
    gain_db: float = 0.0
    hotwords: tuple[str, ...] = ()


class VoicePracticeController(QObject):
    changed = Signal()
    textChanged = Signal()
    completed = Signal(str)
    _result = Signal(int, str, str, str)
    _fault = Signal(int, str)
    _gesture = Signal(int, str)
    _playFinished = Signal(int, str)
    sample_rate = 16000
    maximum_seconds = 30

    def __init__(self, configuration: Callable[[], PracticeConfiguration], parent=None):
        super().__init__(parent)
        self._configuration = configuration
        self._lock = threading.Lock()
        self._armed = threading.Event()
        self._recording = False
        self._processing = False
        self._playing = False
        self._generation = 0
        self._mode = "dictation"
        self._text = ""
        self._undo: list[str] = []
        self._message = "点击开始测试，在这里练习语音输入"
        self._frames: list[np.ndarray] = []
        self._samples = 0
        self._level = 0.0
        self._levels = [0.0] * 32
        self._meter_peak = 0.0
        self._last_audio = 0.0
        self._audio_test_passed = False
        self._last_sound = 0.0
        self._started = 0.0
        self._audio = np.empty(0, dtype=np.float32)
        self._config: PracticeConfiguration | None = None
        self._after_record = ""
        self._microphone = None
        self._cancel_work = threading.Event()
        self._result.connect(self._apply_result)
        self._fault.connect(self._apply_fault)
        self._gesture.connect(self._apply_gesture)
        self._playFinished.connect(self._finish_playback)
        self._timer = QTimer(self)
        self._timer.setInterval(80)
        self._timer.timeout.connect(self._tick)

    @Property(bool, notify=changed)
    def active(self):
        return self._armed.is_set()

    @Property(bool, notify=changed)
    def recording(self):
        return self._recording

    @Property(bool, notify=changed)
    def processing(self):
        return self._processing

    @Property(bool, notify=changed)
    def playing(self):
        return self._playing

    @Property(bool, notify=changed)
    def audioAvailable(self):
        return bool(self._audio.size)

    @Property(float, notify=changed)
    def level(self):
        return self._level if self._recording else 0.0

    @Property("QVariantList", notify=changed)
    def levels(self):
        return list(self._levels)

    @Property(bool, notify=changed)
    def audioTestPassed(self):
        return self._audio_test_passed

    @Property(float, notify=changed)
    def seconds(self):
        return self._samples / self.sample_rate

    @Property(str, notify=changed)
    def message(self):
        return self._message

    @Property(str, notify=changed)
    def mode(self):
        return self._mode

    @Property(str, notify=textChanged)
    def text(self):
        return self._text

    @Slot(str)
    def setText(self, text):
        if not self._processing and not self._recording and self._text != text:
            self._text = text
            self.textChanged.emit()

    @Slot(result=bool)
    def arm(self):
        if self.active:
            return True
        try:
            self._config = self._configuration()
        except Exception as exc:
            self._message = str(exc)
            self.changed.emit()
            return False
        self._generation += 1
        self._cancel_work = threading.Event()
        self._armed.set()
        self._mode = "dictation"
        self._message = "练习已就绪，可以用手势或按钮开始"
        self.changed.emit()
        return True

    @Slot(str, result=bool)
    def start(self, mode="dictation"):
        if self._recording or self._processing or self._playing:
            return False
        if mode not in {"audio", "dictation", "edit"}:
            return False
        if mode == "edit" and not self._text.strip():
            self._message = "请先在练习框输入一句话，再进行语音修改"
            self.changed.emit()
            return False
        if not self.arm():
            return False
        self._generation += 1
        token = self._generation
        self._cancel_work = threading.Event()
        self._mode = mode
        with self._lock:
            self._frames = []
            self._samples = 0
            self._level = 0.0
            self._levels = [0.0] * 32
            self._meter_peak = 0.0
            self._last_audio = 0.0
            self._audio_test_passed = False
            self._recording = True
        self._started = self._last_sound = time.monotonic()
        self._audio = np.empty(0, dtype=np.float32)
        self._message = "正在收音，说完后点击停止" if mode == "audio" else "请说出修改指令" if mode == "edit" else "正在听写，说完后定稿"
        self._timer.start()
        self.changed.emit()
        if not self._config.shared:
            selection = self._config.microphone
            self._spawn(lambda: self._read_microphone(token, selection), "PracticeMicrophone")
        return True

    @staticmethod
    def _spawn(work, name):
        threading.Thread(target=work, name=name, daemon=True).start()

    def _read_microphone(self, token, selection):
        source = MicrophoneSource(selection=selection)
        try:
            with self._lock:
                if token != self._generation or not self._recording:
                    return
                self._microphone = source
            source.open()
            while token == self._generation and self._recording:
                block = source.read(320)
                if block is None:
                    if token != self._generation or not self._recording:
                        break
                    raise RuntimeError("麦克风已停止，请检查输入设备后重试")
                self._feed(block, token)
        except Exception as exc:
            if token == self._generation and self._recording:
                self._fault.emit(token, str(exc))
        finally:
            source.close()
            with self._lock:
                if self._microphone is source:
                    self._microphone = None

    def _release_microphone(self):
        with self._lock:
            source, self._microphone = self._microphone, None
        if source is not None:
            self._spawn(source.close, "PracticeMicrophoneClose")

    def consume_audio(self, block, runtime_busy=False):
        """Called only by the existing audio owner; True isolates this block."""
        if not self._armed.is_set():
            return False
        if runtime_busy:
            self._armed.clear()
            self._fault.emit(self._generation, "请先结束当前输入，再开始练习")
            return False
        if self._config and self._config.shared:
            self._feed(block, self._generation)
        return True

    def _feed(self, block, token):
        incoming = np.asarray(block, dtype=np.float32).reshape(-1)
        if not np.all(np.isfinite(incoming)):
            if token == self._generation and self._recording:
                self._fault.emit(token, "输入设备返回了无效音频，请重新测试")
            return
        with self._lock:
            if token != self._generation or not self._recording:
                return
            remaining = self.sample_rate * self.maximum_seconds - self._samples
            audio = incoming[:remaining].copy()
            if not audio.size:
                return
            self._frames.append(audio)
            self._samples += audio.size
            rms = float(np.sqrt(np.mean(audio * audio)))
            self._level = min(1.0, rms * 12)
            # Display real PCM energy on a logarithmic scale so soft speech is
            # visible too. Keep the recording and recognition samples untouched.
            visible_level = float(max(0.0, min(1.0, (20 * np.log10(max(rms, 1e-6)) + 60) / 60)))
            self._meter_peak = max(self._meter_peak, visible_level)
            self._last_audio = time.monotonic()
            if rms > 0.001:
                self._last_sound = time.monotonic()

    def consume_gesture(self, event):
        if not self._armed.is_set():
            return False
        self._gesture.emit(self._generation, str(event.name))
        return True

    @Slot(int, str)
    def _apply_gesture(self, token, name):
        if token != self._generation or not self.active or not self._config:
            return
        bindings = self._config.bindings
        if name in bindings.undo:
            self.undo()
        elif self._processing or self._playing or self._mode == "audio":
            return
        elif self._recording and name in bindings.end:
            self.finish()
        elif not self._recording and name in bindings.confirm:
            self.start("dictation")
        elif name == "swipe-down":
            if self._recording:
                self._after_record = "polish"
                self.finish()
            else:
                self.polish()
        elif name in bindings.switch_mode:
            if not self._recording:
                self.start("edit")
        elif name == "swipe-up":
            if self._recording:
                self.finish()
            self._message = "练习内容已留在虚拟文本框中"
            self.changed.emit()

    def _tick(self):
        if not self._recording:
            return
        with self._lock:
            self._levels = self._levels[1:] + [self._meter_peak]
            self._meter_peak = 0.0
            if time.monotonic() - self._last_audio > 0.25:
                self._level = 0.0
        if time.monotonic() - self._started >= self.maximum_seconds:
            self.finish()
        else:
            if time.monotonic() - self._last_sound > 3:
                self._message = "未检测到声音，请检查静音、输入设备和麦克风权限"
            elif self._mode == "audio":
                self._message = "正在接收声音" if self._level > 0.012 else "等待声音…"
            self.changed.emit()

    @Slot()
    def finish(self):
        if not self._recording:
            return
        self._timer.stop()
        with self._lock:
            self._recording = False
            audio = np.concatenate(self._frames) if self._frames else np.empty(0, dtype=np.float32)
            self._frames = []
        self._release_microphone()
        self._audio = audio
        if audio.size < self.sample_rate // 5 or float(np.max(np.abs(audio), initial=0)) < 0.001:
            self._message = "没有收到有效声音，请检查输入源后重试"
            self._after_record = ""
            if self._mode == "audio":
                self._armed.clear()
            self.changed.emit()
            return
        if self._mode == "audio":
            self._armed.clear()
            self._audio_test_passed = True
            self._message = "录音完成，可以回放检查收音"
            self.completed.emit("audio")
        else:
            self._processing = True
            self._message = "正在识别…"
            token, config, mode, target, cancel = self._generation, self._config, self._mode, self._text, self._cancel_work
            self._spawn(lambda: self._recognize(token, config, mode, target, audio, cancel), "PracticeRecognition")
        self.changed.emit()

    def _recognize(self, token, config, mode, target, audio, cancel):
        backend = None
        try:
            if cancel.is_set():
                return
            wave = np.clip(audio * (10 ** (config.gain_db / 20)), -1, 1)
            if asr_backend_kind(config.backend) == "batch":
                backend = create_asr_backend(config.backend, config.asr)
                text = backend.transcribe(wave)
            else:
                backend = create_streaming_asr_backend(config.backend, config.asr)
                setter = getattr(backend, "set_cancel_event", None)
                if callable(setter):
                    setter(cancel)
                context = getattr(backend, "set_session_context", None)
                if config.backend == "volcengine" and callable(context):
                    context({"hotwords": list(config.hotwords)})
                backend.start()
                for offset in range(0, wave.size, 3200):
                    if cancel.is_set():
                        return
                    backend.feed(wave[offset:offset + 3200])
                text = backend.finish(wave)
            if cancel.is_set():
                return
            text = str(text or "").strip()
            if not text:
                raise ValueError("未识别到文字，请重新录音")
            if mode == "edit":
                text = self._process_text(text, mode, config.llm, target)
            self._result.emit(token, mode, text, "")
        except Exception as exc:
            if not cancel.is_set():
                self._result.emit(token, mode, "", str(exc))
        finally:
            cleanup = getattr(backend, "abort", None) or getattr(backend, "close", None)
            if callable(cleanup):
                cleanup()

    @staticmethod
    def _process_text(text, mode, settings, target=""):
        processor = OpenAICompatibleTextProcessor()
        try:
            return processor.process(text, mode, replace(settings, enabled=True), target_text=target)
        finally:
            processor.close()

    @Slot()
    def polish(self):
        if self._processing or self._recording or self._playing or not self._text.strip():
            return
        if not self.arm():
            return
        self._generation += 1
        token, text, config, cancel = self._generation, self._text, self._config, self._cancel_work
        self._processing = True
        self._message = "正在润色当前练习句…"
        self.changed.emit()

        def work():
            try:
                result = self._process_text(text, "polish", config.llm)
                if not cancel.is_set():
                    self._result.emit(token, "polish", result, "")
            except Exception as exc:
                if not cancel.is_set():
                    self._result.emit(token, "polish", "", str(exc))
        self._spawn(work, "PracticePolish")

    @Slot(int, str, str, str)
    def _apply_result(self, token, mode, text, error):
        if token != self._generation or not self.active:
            return
        self._processing = False
        if error:
            self._message = "练习未完成：" + error
            self._after_record = ""
        else:
            self._undo.append(self._text)
            self._undo = self._undo[-20:]
            self._text = text
            self._message = {"dictation": "本句已定稿", "edit": "语音修改完成", "polish": "本句已润色"}.get(mode, "完成")
            self.textChanged.emit()
            self.completed.emit(mode)
        self.changed.emit()
        if not error and self._after_record == "polish":
            self._after_record = ""
            self.polish()

    @Slot(int, str)
    def _apply_fault(self, token, message):
        if token == self._generation:
            self.cancel()
            self._message = message
            self.changed.emit()

    @Slot()
    def undo(self):
        if self._recording or self._processing:
            self.cancel()
            return
        if self._undo:
            self._text = self._undo.pop()
            self.textChanged.emit()
            self._message = "已撤回上一次练习操作"
            self.changed.emit()

    @Slot()
    def play(self):
        if not self.audioAvailable or self._recording or self._processing:
            return
        if self._playing:
            self.cancel()
            return
        if not self.arm():
            return
        self._generation += 1
        token, audio, cancel = self._generation, self._audio.copy(), self._cancel_work
        self._playing = True
        self._message = "正在回放录音"
        self.changed.emit()

        def work():
            error = ""
            try:
                import sounddevice as sd
                # Own stream only; sd.stop() would also stop unrelated playback.
                with sd.OutputStream(samplerate=self.sample_rate, channels=1, dtype="float32") as stream:
                    for offset in range(0, audio.size, 1600):
                        if cancel.is_set():
                            break
                        stream.write(audio[offset:offset + 1600])
            except Exception as exc:
                error = str(exc)
            self._playFinished.emit(token, error)
        self._spawn(work, "PracticePlayback")

    @Slot(int, str)
    def _finish_playback(self, token, error):
        if token != self._generation:
            return
        self._playing = False
        self._armed.clear()
        self._message = "回放失败：" + error if error else "回放结束"
        self.changed.emit()

    @Slot()
    def cancel(self):
        had_session = self.active or self._recording or self._processing or self._playing or self.audioAvailable
        self._armed.clear()
        self._cancel_work.set()
        self._generation += 1
        with self._lock:
            self._recording = False
            self._frames = []
            self._level = 0.0
            self._levels = [0.0] * 32
            self._meter_peak = 0.0
            self._audio_test_passed = False
        self._processing = self._playing = False
        self._release_microphone()
        self._after_record = ""
        self._audio = np.empty(0, dtype=np.float32)
        self._timer.stop()
        if had_session:
            self._message = "练习已结束，点击开始可以重新练习"
        self.changed.emit()
