from __future__ import annotations

import os
from pathlib import Path
import signal
import sys


def main(argv: list[str] | None = None) -> int:
    from ..runtime_paths import configure_runtime_environment, resource_root

    configure_runtime_environment()
    from ..diagnostics.runtime_diagnostics import configure_diagnostics
    from ..runtime_paths import is_frozen
    diagnostic_capture = configure_diagnostics(tee=not is_frozen())
    try:
        from PySide6.QtCore import QTimer, QUrl
        from PySide6.QtGui import QAction, QIcon
        from PySide6.QtQml import QQmlApplicationEngine
        from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon
    except ImportError as exc:
        raise SystemExit(
            'Mythlink UI requires PySide6. Install with: pip install -e ".[ui]"'
        ) from exc

    from .controller import AppController

    diagnostic_capture.install_qt()

    startup_probe = os.environ.get("PROXIMIC_STARTUP_PROBE", "").strip() == "1"
    from proximic_ring.agent_workflow.ui.message_document import initialize_web_engine
    initialize_web_engine()
    print("[startup] creating QApplication")
    app = QApplication(list(sys.argv if argv is None else argv))
    app.setApplicationName("Mythlink")
    app.setApplicationDisplayName("Mythlink")
    app.setOrganizationName("Mythlink")

    from ..platforms.macos.mac_app_activation import configure_app_activation
    configure_app_activation(background=startup_probe)

    base = Path(__file__).resolve().parent
    icon_path = base / "assets" / "mythlink-app.png"
    icon = QIcon(str(icon_path))
    tray_asset = "mythlink-app.svg" if sys.platform == "darwin" else "mythlink-mark.svg"
    tray_icon = QIcon(str(base / "assets" / tray_asset))
    if icon.isNull():
        icon = tray_icon
    app.setWindowIcon(icon)

    from ..agent_workflow.agent.background_flow import shutdown_background_tasks
    app.aboutToQuit.connect(shutdown_background_tasks)
    controller = AppController()
    if not startup_probe:
        controller.browserExtensions.start()
    app.aboutToQuit.connect(controller.browserExtensions.close)
    app.aboutToQuit.connect(controller.voicePractice.cancel)
    print("[startup] controller ready")
    voice_action_hotkeys = None
    if sys.platform == "win32":
        try:
            from ..voice.voice_actions import WindowsVoiceActionHotkeys

            voice_action_hotkeys = WindowsVoiceActionHotkeys(
                controller.dispatchVoiceAction,
                mode_switch_shortcut=(
                    lambda: controller.modeCorrectionShortcut
                ),
                is_interaction_active=lambda: controller.interactionCanCancel,
                is_mode_correction_active=lambda: (
                    controller.modeCorrectionHotkeyAvailable
                    or controller.processingModeCorrectionAvailable
                ),
                is_undo_active=lambda: controller.nativeUndoAvailable,
            )
            app.aboutToQuit.connect(voice_action_hotkeys.close)
        except BaseException as exc:
            print(f"[voice-actions] 全局交互快捷键不可用：{exc}", file=sys.stderr)
    # On macOS the selected input method handles F8/Esc directly. No global
    # event tap or Accessibility permission is needed for speech interactions.
    engine = QQmlApplicationEngine()
    from .desktop.application_icons import install_application_icons
    install_application_icons(engine)
    engine.rootContext().setContextProperty("appController", controller)
    qml_errors: list[str] = []
    engine.warnings.connect(
        lambda warnings: qml_errors.extend(str(item) for item in warnings)
    )
    engine.load(QUrl.fromLocalFile(str(base / "qml" / "Main.qml")))
    if not engine.rootObjects():
        detail = "\n".join(qml_errors) or "QML engine did not create a root window"
        raise RuntimeError(f"主界面加载失败：{detail}")
    window = engine.rootObjects()[0]
    from .shared.window_chrome import configure_main_window_chrome

    configure_main_window_chrome(window)
    controller.ringGestures.windowSelector.set_main_window(window)
    from proximic_ring.agent_workflow.ui.agent_overlay import AgentOverlay
    agent_overlay = AgentOverlay(controller.agent, controller, app)
    from .shared.main_window_activation import MainWindowActivation
    main_activation = MainWindowActivation(window, agent_overlay, app)
    from .shared.notifications import install_ring_disconnect_notice

    install_ring_disconnect_notice(window, controller)
    from .gestures.gesture_hud import GestureHud

    gesture_hud = GestureHud(app, diagnostic=controller._append_background_diagnostic)
    from .gestures.gesture_trigger_overlay import GestureTriggerOverlay
    gesture_trigger = GestureTriggerOverlay(controller.gestureTrigger, app,
        diagnostic=controller._append_background_diagnostic)
    controller.ringGestures.showRequested.connect(
        lambda mode, message: gesture_hud.show_mode(
            mode, message=message, global_bindings=controller.ringGestures.globalBindings,
            global_labels=controller.ringGestures.globalActionLabels)
    )
    controller.ringGestures.sceneHudRequested.connect(
        lambda mode, actions: gesture_hud.show_mode(mode, scene_actions=actions,
            global_bindings=controller.ringGestures.globalBindings, global_labels=controller.ringGestures.globalActionLabels)
    )
    controller.ringGestures.hideRequested.connect(gesture_hud.hide)
    from .desktop.window_selector_overlay import WindowSelectorOverlay
    window_selector = WindowSelectorOverlay(controller.ringGestures.windowSelector, app,
                                            diagnostic=controller._append_background_diagnostic)
    print("[startup] QML root window ready")
    from .desktop.application_onboarding import ApplicationOnboarding
    from .desktop.application_onboarding_overlay import ApplicationOnboardingOverlay
    from .shared.feedback_availability import feedback_block_reason
    application_onboarding = ApplicationOnboarding(controller.appGestures.catalog, app,
        busy=lambda: feedback_block_reason(controller))
    application_suggestion = ApplicationOnboardingOverlay(application_onboarding, app)
    from .scenes.scene_notice_controller import SceneNoticeController
    from .scenes.scene_notice_overlay import SceneNoticeOverlay
    scene_notice = SceneNoticeController(controller.appGestures, app,
        busy=lambda: feedback_block_reason(controller, suggestion=application_onboarding))
    scene_notice_overlay = SceneNoticeOverlay(scene_notice, app)
    # Load model weights and seed both stable prompt prefixes after the first
    # frame instead of making the user's first utterance pay this cost.
    if startup_probe:
        # Agent QML loads lazily during normal use. Exercise its real creation
        # path too: Main.qml can succeed even when an Agent-only plugin is
        # missing from the frozen bundle. Keep the card hidden; no task starts.
        agent_overlay._create("task")
        print("[startup] Agent workspace QML ready")
        from types import SimpleNamespace
        # Reach the shared focus gate with the real release controllers. The
        # busy endpoint returns before native focus IO or any queued action;
        # no audio stream or physical ring is needed for this package check.
        bindings = controller._gesture_bindings
        for name in sorted(set(bindings.confirm + bindings.end) - {""}):
            if not controller.ringGestures.filter(SimpleNamespace(name=name), True,
                                                  controller._disconnect_event):
                raise RuntimeError(f"Packaged voice gesture endpoint rejected: {name}")
        print("[startup] Ring voice gesture endpoints ready")
        if sys.platform == "darwin":
            from ..platforms.macos.mac_permissions import permission_status_report
            report = permission_status_report(app, controller._settings)
            if report["backend_errors"]:
                raise RuntimeError("macOS permission backend unavailable: " + "; ".join(report["backend_errors"]))
            print("[startup] macOS permission backends ready (read-only)")
            from ..desktop.app_shortcuts import verify_native_api
            verify_native_api()
            print("[startup] macOS browser scene backend ready (read-only)")
        # Packaging CI sets this flag to prove the frozen executable can import
        # the application, QML, and the dynamically loaded ASR modules.
        from ..asr.backends.funasr_nano import FunASRNanoStreamingASR
        from ..asr.backends.streaming_sensevoice import StreamingSenseVoiceASR

        StreamingSenseVoiceASR._load_external_class(
            resource_root() / "third_party" / "streaming-sensevoice"
        )
        nano_repo = resource_root() / "third_party" / "Fun-ASR"
        nano_probe = object.__new__(FunASRNanoStreamingASR)
        nano_probe.repo_path = nano_repo
        nano_probe._load_external_class(nano_repo / "model.py")
        __import__("transformers.models.qwen3.modeling_qwen3")
        print("[startup] packaged ASR imports ready")
        QTimer.singleShot(300, app.quit)
    else:
        # The bundle initially launches as an agent to keep workers out of the
        # Dock. Give the promoted main window focus so native onboarding can run.
        QTimer.singleShot(0, window.requestActivate)
        QTimer.singleShot(150, controller.appGestures.catalog.initializeInstalledApplications)
        QTimer.singleShot(300, controller.permissionSetup.startIfNeeded)
        QTimer.singleShot(600, controller.warmLocalModel)
        QTimer.singleShot(2500, application_onboarding.start)
        QTimer.singleShot(2500, scene_notice.start)
        QTimer.singleShot(5000, controller.agent.historyRecommendations.start)

    tray_available = QSystemTrayIcon.isSystemTrayAvailable()
    controller.setTrayAvailable(tray_available)
    # Closing the main window is an explicit application exit.  The tray is a
    # convenience controller, not a reason to leave an invisible process alive.
    app.setQuitOnLastWindowClosed(True)
    tray = None
    if tray_available:
        if sys.platform == "darwin":
            try:
                from .shared.menu_bar import MacMenuBar

                tray = MacMenuBar(tray_icon)
            except Exception as exc:
                print(f"[menu-bar] 原生文字不可用，保留图标菜单：{exc}", file=sys.stderr)
        if tray is None:
            tray = QSystemTrayIcon(tray_icon, app)
        menu = QMenu()
        show_action = QAction("显示主窗口", menu)
        connection_action = QAction("连接设备", menu)
        recognition_action = QAction("开启语音识别", menu)
        touchpad_action = QAction("开启触摸板", menu)
        gesture_menu_action = QAction("显示手势提示", menu)
        agent_action = QAction("唤起 Agent", menu)
        quit_action = QAction("退出", menu)
        menu.addAction(show_action)
        menu.addAction(connection_action)
        menu.addAction(recognition_action)
        menu.addAction(touchpad_action)
        menu.addSeparator()
        menu.addAction(gesture_menu_action)
        menu.addAction(agent_action)
        agent_action.triggered.connect(controller.agent.show)
        menu.addSeparator()
        menu.addAction(quit_action)
        tray.setContextMenu(menu)

        def show_window() -> None:
            main_activation.show()

        def toggle_connection() -> None:
            if controller.connected:
                controller.disconnectDevice()
            else:
                show_window()
                if controller.canReconnect:
                    controller.reconnectDevice()
                else:
                    controller.requestDevicePicker()

        def refresh_tray() -> None:
            connection_action.setText(
                "断开设备"
                if controller.connected
                else ("重新连接设备" if controller.canReconnect else "选择并连接设备")
            )
            connection_action.setEnabled(
                controller.statusKind != "stopping"
                if controller.connected
                else not controller.busy and not controller.scanBusy
            )
            recognition_action.setText(
                "暂停语音识别" if controller.recognitionEnabled else "开启语音识别"
            )
            recognition_action.setEnabled(controller.connected and not controller.busy)
            touchpad = controller.touchpad
            touchpad_action.setText(
                "取消开启触摸板" if touchpad.state == "starting"
                else "正在断开触摸板…" if touchpad.state == "stopping"
                else "断开触摸板" if touchpad.active else "开启触摸板"
            )
            touchpad_action.setEnabled(
                touchpad.supported
                and (touchpad.active or (touchpad.available and not touchpad.busy))
            )
            tray.setToolTip(f"Mythlink · {controller.statusTitle}")
            if hasattr(tray, "setConnectionStatus"):
                tray.setConnectionStatus(
                    controller.connected, controller.batteryAvailable,
                    controller.batteryPercentage, controller.batteryCharging,
                    controller.menuBarStatusInfo,
                )

        show_action.triggered.connect(show_window)
        connection_action.triggered.connect(toggle_connection)
        recognition_action.triggered.connect(controller.toggleRecognition)
        touchpad_action.triggered.connect(controller.touchpad.toggle)
        gesture_menu_action.triggered.connect(controller.ringGestures.showMenu)
        quit_action.triggered.connect(controller.requestQuit)
        controller.connectedChanged.connect(refresh_tray)
        controller.ringGestures.changed.connect(refresh_tray)
        controller.touchpad.changed.connect(refresh_tray)
        controller.recognitionEnabledChanged.connect(refresh_tray)
        controller.busyChanged.connect(refresh_tray)
        controller.scanBusyChanged.connect(refresh_tray)
        controller.settingsChanged.connect(refresh_tray)
        controller.reconnectAvailabilityChanged.connect(refresh_tray)
        controller.statusChanged.connect(refresh_tray)
        controller.batteryChanged.connect(refresh_tray)
        if isinstance(tray, QSystemTrayIcon):
            tray.activated.connect(
                lambda reason: show_window()
                if reason in (
                    QSystemTrayIcon.ActivationReason.Trigger,
                    QSystemTrayIcon.ActivationReason.DoubleClick,
                )
                else None
            )
        app.aboutToQuit.connect(tray.hide)
        refresh_tray()
        tray.show()

    # A small Qt timer gives Python regular opportunities to dispatch console
    # signals while the native Qt event loop is running.
    heartbeat = QTimer(app)
    heartbeat.setInterval(200)
    heartbeat.timeout.connect(lambda: None)
    heartbeat.start()

    def handle_console_signal(_signum, _frame) -> None:
        controller.requestQuit()

    signal.signal(signal.SIGINT, handle_console_signal)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, handle_console_signal)

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
