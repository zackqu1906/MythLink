"""Dismissing a keyed companion must not raise the main application window."""
import os
import subprocess
import sys

import pytest

from test_agent_focus_native import SCRIPT


@pytest.mark.skipif(sys.platform != 'darwin' or os.environ.get('PROXIMIC_TEST_AGENT_NATIVE') != '1',
                    reason='opt-in native companion dismissal')
def test_companion_hide_keeps_main_in_place(tmp_path):
    source = SCRIPT.replace("print('PASS focus preserves main window: ' + state, flush=True)", """
        print('PASS focus preserves main window: ' + state, flush=True)
        agent.hide()
        QTest.qWait(250)
        visible_ids = [int(w['kCGWindowNumber']) for w in Quartz.CGWindowListCopyWindowInfo(
            Quartz.kCGWindowListOptionOnScreenOnly, 0)]
        assert (main.isVisible(), main.windowState()) == before, ('dismiss changed main', state)
        if state == 'visible':
            assert visible_ids.index(main_id) > visible_ids.index(cover_id), ('dismiss raised main', state)
        else:
            assert main_id not in visible_ids, ('dismiss showed main', state)
        agent.show()
        AgentOverlay.focus(host, 'task')
        QTest.qWait(150)
        assert agent.isActive() and native(agent).isKeyWindow()
        print('PASS companion dismiss and reopen: ' + state, flush=True)
""")
    script = tmp_path/'companion_dismiss_probe.py'
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
    result = subprocess.run([sys.executable, str(script), str(qml), 'explicit'],
        text=True, capture_output=True, timeout=30,
        env={**os.environ, 'QT_QPA_PLATFORM': 'cocoa'})
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.count('PASS companion dismiss and reopen') == 3
