"""Real AppKit IME focus round trip using disposable windows, no source change."""
import os
from pathlib import Path
import subprocess
import sys

import pytest

from test_agent_focus_native import SCRIPT


@pytest.mark.skipif(sys.platform != 'darwin' or os.environ.get('PROXIMIC_TEST_AGENT_NATIVE') != '1',
                    reason='opt-in native Agent/IME handoff')
def test_ime_handoff_keeps_agent_key_and_main_window_order(tmp_path):
    repo = Path(__file__).resolve().parents[1]
    main = tmp_path/'main.swift'
    main.write_text('''import AppKit
import Foundation
let args = CommandLine.arguments
selectSourceSession(pid: Int32(args[2])!, bundle: args[3], modeID: "fixture",
    refreshOnly: true, hostRestoresFocus: args.contains("--host-restores-focus"),
    currentSource: { "fixture" }, select: {})
''')
    helper = tmp_path/'FocusHelper'
    build = subprocess.run(['swiftc', '-module-cache-path', str(tmp_path/'modules'),
        str(repo/'scripts/InputSourceSelection.swift'), str(main), '-o', str(helper)],
        text=True, capture_output=True, timeout=60)
    assert build.returncode == 0, build.stderr
    # Extend the existing real-window entry test at the point where Agent is
    # keyed over the cover app. Observe the main window throughout the handoff.
    source = SCRIPT.replace("agent = QQuickView()", "agent = QQuickView()\nagent.setObjectName('agent-task')").replace(
        "print('PASS focus preserves main window: ' + state, flush=True)",
        """print('PASS focus preserves main window: ' + state, flush=True)
        from proximic_ring.ui.voice.input_source_activation import InputSourceActivation
        activation = InputSourceActivation(command=[sys.argv[3]], timeout_ms=2000)
        host.host._inline_input._source_activation = activation
        results = []
        activation.event.connect(results.append)
        activation.recover((AppKit.NSRunningApplication.currentApplication().bundleIdentifier() or '', os.getpid()))
        for _ in range(400):
            QTest.qWait(5)
            visible_ids = [int(w['kCGWindowNumber']) for w in Quartz.CGWindowListCopyWindowInfo(
                Quartz.kCGWindowListOptionOnScreenOnly, 0)]
            assert (main.isVisible(), main.windowState()) == before, ('handoff changed main state', state)
            if state == 'visible':
                assert visible_ids.index(main_id) > visible_ids.index(cover_id), ('handoff raised main', state)
            else:
                assert main_id not in visible_ids, ('handoff showed main', state)
            if results: break
        assert results and results[-1]['event'] == 'selected', results
        assert agent.isActive() and native(agent).isKeyWindow() and controller.focused, ('lost Agent input', state)
        assert agent.rootObject().property('editorFocused')
        activation.cancel()
        host.host._inline_input._source_activation = None
        print('PASS IME handoff preserves Agent and main: ' + state, flush=True)""")
    script = tmp_path/'agent_ime_probe.py'
    script.write_text(source)
    qml = tmp_path/'Editor.qml'
    qml.write_text('''import QtQuick
Rectangle {
    width: 300; height: 180; color: "#333333"
    property alias text: editor.text
    property alias selection: editor.selectedText
    property alias caret: editor.cursorPosition
    property alias editorFocused: editor.activeFocus
    function focusEditor() { editor.forceActiveFocus() }
    TextEdit { id: editor; anchors.fill: parent; color: "white" }
}''')
    result = subprocess.run([sys.executable, str(script), str(qml), 'explicit', str(helper)],
        text=True, capture_output=True, timeout=35,
        env={**os.environ, 'QT_QPA_PLATFORM':'cocoa'})
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.count('PASS IME handoff') == 3, result.stdout
