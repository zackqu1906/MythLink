import QtQuick
import QtQuick.Controls
import QtQuick.Controls.Basic as Basic
import QtQuick.Layouts

ColumnLayout {
    id: editor
    required property var practice
    property bool showActions: true
    property string editorName: "voiceTrialEditor"
    property bool attempted: false
    spacing: 8
    FigmaTheme { id: theme }
    Rectangle {
        objectName: "voiceTrialTextTarget"
        Layout.fillWidth: true; Layout.fillHeight: true; Layout.minimumHeight: 96
        color: theme.background; radius: 8; border.color: input.activeFocus ? theme.primary : theme.line
        ScrollView {
            anchors.fill: parent; anchors.margins: 12
            clip: true; contentWidth: availableWidth
            Basic.TextArea {
                id: input
                objectName: editor.editorName
                text: editor.practice.text
                onTextChanged: if (text !== editor.practice.text) editor.practice.setText(text)
                readOnly: editor.practice.recording || editor.practice.processing
                placeholderText: "随便说些什么……"
                font.family: theme.family; font.pixelSize: 14
                color: theme.text; placeholderTextColor: theme.muted
                wrapMode: TextEdit.Wrap; selectByMouse: true; padding: 0
                topInset: 0; bottomInset: 0; background: null
            }
        }
    }
    Label {
        objectName: "voicePracticeStatus"
        visible: (editor.attempted || editor.practice.active) && editor.practice.mode !== "audio"
        Layout.fillWidth: true; text: editor.practice.message
        color: theme.muted; font.pixelSize: 14; wrapMode: Text.Wrap
    }
    Flow {
        visible: editor.showActions && editor.practice.text.length > 0
        Layout.fillWidth: true; spacing: 8
        FigmaAction { text: "润色当前句"; symbol: "swipe-down"; enabled: !editor.practice.recording && !editor.practice.processing && !editor.practice.playing; onClicked: editor.practice.polish() }
        FigmaAction { text: "语音修改"; symbol: "swipe-right"; enabled: !editor.practice.recording && !editor.practice.processing && !editor.practice.playing; onClicked: editor.practice.start("edit") }
        FigmaAction { text: "撤回"; enabled: !editor.practice.playing; onClicked: editor.practice.undo() }
    }
    RowLayout {
        visible: editor.showActions
        Layout.fillWidth: true; spacing: 8
        FigmaAction {
            objectName: "voicePracticeStartButton"
            Layout.fillWidth: true; primary: true
            font.pixelSize: 16
            text: editor.practice.processing ? "正在处理…" : editor.practice.recording && editor.practice.mode !== "audio" ? "停止并定稿" : "开始测试"
            enabled: !editor.practice.processing && !editor.practice.playing && !(editor.practice.recording && editor.practice.mode === "audio")
            onClicked: { editor.attempted = true; editor.practice.recording ? editor.practice.finish() : editor.practice.start("dictation") }
        }
        FigmaAction { text: "结束练习"; visible: editor.practice.active && editor.practice.mode !== "audio"; onClicked: editor.practice.cancel() }
    }
}
