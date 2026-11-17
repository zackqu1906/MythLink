import QtQuick
import QtQuick.Controls
import QtQuick.Controls.Basic as Basic
import QtQuick.Layouts

Rectangle {
    id: editor
    objectName: "voiceTrialTextTarget"
    Layout.minimumHeight: 96
    color: theme.background; radius: 8; border.color: input.activeFocus ? theme.primary : theme.line
    FigmaTheme { id: theme }
    ScrollView {
        anchors.fill: parent; anchors.margins: 12
        clip: true; contentWidth: availableWidth
        Basic.TextArea {
            id: input
            objectName: "voiceTrialEditor"
            // The standard editor owns its document, composition and undo history.
            // Audio checks and tutorial practice must not lock or replace its text.
            textFormat: TextEdit.PlainText
            placeholderText: "点击这里，直接打字或使用语音输入……"
            Accessible.name: "语音测试文本框"
            font.family: theme.family; font.pixelSize: 14
            color: theme.text; placeholderTextColor: theme.muted
            wrapMode: TextEdit.Wrap; selectByMouse: true; padding: 0
            topInset: 0; bottomInset: 0; background: null
        }
    }
}
