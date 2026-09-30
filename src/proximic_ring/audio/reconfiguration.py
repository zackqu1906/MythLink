"""Idle audio changes preserve the BLE session and existing ASR sinks."""
from dataclasses import replace
import threading

from ..live_audio_settings import AudioConfiguration


class AudioInputPipeline:
    def __init__(self, ring, microphone, detector, controller, settings, *,
                 microphone_factory, detector_factory, on_state, on_push_to_talk=None):
        self.ring = ring
        self.microphone = microphone
        self.detector = detector
        self.controller = controller
        self.settings = settings
        self.configuration = AudioConfiguration(settings.audio_source, settings.microphone_device,
                                                settings.speech_control_mode)
        self._microphone_factory = microphone_factory
        self._detector_factory = detector_factory
        self._cached_detector = detector
        self._manual_active = getattr(controller, "manual_active", None)
        self._extra_hotkey = None
        self._on_state = on_state
        self._on_push_to_talk = on_push_to_talk
        self._microphone_lock = threading.Lock()

    @property
    def audio_source(self):
        return self.microphone if self.microphone is not None else self.ring

    def close_microphone(self):
        with self._microphone_lock:
            microphone, self.microphone = self.microphone, None
        if microphone is not None:
            microphone.close()

    def close(self):
        try:
            self.close_microphone()
        finally:
            if self._extra_hotkey is not None:
                self._extra_hotkey.close()
                self._extra_hotkey = self._manual_active = None

    def initialize_controls(self):
        if self.settings.push_to_talk:
            self._configure_gate(self.configuration, self.detector)

    def _check_connected(self, stop):
        if stop.is_set():
            raise RuntimeError("音频切换已取消")
        if self.ring.error is not None:
            raise RuntimeError(str(self.ring.error))

    def _open_capture(self, configuration, stop):
        self._check_connected(stop)
        if configuration.audio_source == "ring":
            self.ring.set_audio_enabled(True)
        else:
            microphone = self._microphone_factory(selection=configuration.microphone_device)
            with self._microphone_lock:
                self.microphone = microphone
            # close() can interrupt an in-progress read during disconnect.
            self._check_connected(stop)
            microphone.open()
            if microphone.read(320) is None:
                raise RuntimeError("电脑麦克风未返回音频")
            if microphone.error is not None:
                raise RuntimeError(str(microphone.error))
        self._check_connected(stop)

    def _configure_gate(self, configuration, detector):
        gesture = configuration.speech_control_mode == "gesture"
        if gesture and self._extra_hotkey is not None:
            self._extra_hotkey.close()
            self._extra_hotkey = self._manual_active = None
        if not gesture and self.settings.push_to_talk and self._manual_active is None:
            from ..push_to_talk import WindowsPushToTalkHotkey
            self._extra_hotkey = WindowsPushToTalkHotkey(
                on_error=self._on_state,
                on_change=lambda active: self._on_push_to_talk(active)
                    if self._on_push_to_talk is not None and self.configuration.speech_control_mode == "proximity" else None,
            )
            self._manual_active = self._extra_hotkey.is_active
        self.controller.configure_idle(
            start_on_gesture=gesture,
            pre_roll_s=self.settings.asr_pre_roll_s,
            stage2_delay_s=0.0 if gesture else detector.config.stage2_delay_s,
            end_on_tap=self.settings.asr_end_on_tap,
            manual_active=None if gesture else self._manual_active,
        )
        if detector is not None:
            detector.reset()

    def apply(self, configuration, stop):
        """Return an error only after restoring old capture; raise if recovery fails.

        Caller has gated new speech and verified the entire preceding turn is
        idle. No flush of an active sentence, backend restart or session-ID reset.
        """
        previous = self.configuration
        previous_detector = self.detector
        if self.controller.gesture_busy:
            raise RuntimeError("当前语句尚未结束，不能切换音频设置")
        if (configuration.audio_source == previous.audio_source == "ring"
                and configuration.speech_control_mode == previous.speech_control_mode):
            # Selecting an inactive computer device only changes its preference.
            self.configuration = configuration
            return ""
        try:
            self.ring.set_audio_enabled(False)
            self.close_microphone()
            self._check_connected(stop)
            detector = None
            if configuration.speech_control_mode == "proximity":
                if self._cached_detector is None:
                    options = replace(self.settings, speech_control_mode="proximity").to_namespace()
                    self._cached_detector = self._detector_factory(options)
                detector = self._cached_detector
            self._check_connected(stop)
            self._open_capture(configuration, stop)
            self._configure_gate(configuration, detector)
            self.configuration, self.detector = configuration, detector
            if configuration.speech_control_mode == "gesture" and self._on_push_to_talk is not None:
                self._on_push_to_talk(False)
            return ""
        except Exception as error:
            self._check_connected(stop)
            try:
                self.ring.set_audio_enabled(False)
                self.close_microphone()
                self._open_capture(previous, stop)
                self._configure_gate(previous, previous_detector)
            except Exception as recovery_error:
                raise RuntimeError(f"音频切换失败且无法恢复原设备：{recovery_error}") from error
            return (str(error).strip() or "无法应用所选音频设置").splitlines()[0]
