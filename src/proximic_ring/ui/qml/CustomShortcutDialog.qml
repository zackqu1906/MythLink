import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Dialog {
    id: dialog
    required property var service
    signal actionChosen(var action)
    property string capturedShortcut: ""
    property bool manualInput: false
    readonly property string shortcutValue: manualInput ? typedShortcut.text.trim() : capturedShortcut
    property string errorText: ""
    objectName: "customShortcutDialog"
    title: "自定义快捷键"
    modal: true; popupType: Popup.Item
    closePolicy: Popup.CloseOnEscape
    padding: 24
    enter: Transition { NumberAnimation { property: "opacity"; from: 0; to: 1; duration: 100 } }
    exit: Transition { NumberAnimation { property: "opacity"; from: 1; to: 0; duration: 80 } }
    UiTheme { id: theme }
    onAboutToShow: { actionName.text = ""; capturedShortcut = ""; typedShortcut.text = ""; manualInput = false; errorText = "" }
    onOpened: actionName.forceActiveFocus()
    onAboutToHide: { shortcut.focus = false; service.recording = false }
    background: Rectangle { radius: 16; color: theme.surface; border.color: theme.line }
    header: Label {
        text: dialog.title; color: theme.text; font.pixelSize: 21; font.weight: Font.DemiBold
        leftPadding: 24; rightPadding: 24; topPadding: 24; bottomPadding: 8
    }
    contentItem: ColumnLayout {
        spacing: 14
        Label {
            Layout.fillWidth: true
            text: "先在目标应用中确认或设置快捷键，再在这里录入相同的组合键。"
            wrapMode: Text.Wrap; color: theme.muted; font.pixelSize: 13
        }
        Label { text: "动作名称"; color: theme.text; font.pixelSize: 13 }
        UiTextField {
            id: actionName
            objectName: "customShortcutName"
            Layout.fillWidth: true; implicitHeight: 42
            hintText: "例如：上一个任务"; maximumLength: 120
            color: theme.text
            background: Rectangle { radius: 8; color: theme.surface; border.color: actionName.activeFocus ? theme.primary : theme.line }
        }
        RowLayout {
            Layout.fillWidth: true
            Label { text: "快捷键"; color: theme.text; font.pixelSize: 13 }
            Item { Layout.fillWidth: true }
            UiAction {
                objectName: "customShortcutEntryMode"
                text: dialog.manualInput ? "录制按键" : "手动输入"; quiet: true; implicitHeight: 28
                onClicked: {
                    if (dialog.manualInput) dialog.capturedShortcut = typedShortcut.text.trim()
                    else typedShortcut.text = dialog.capturedShortcut
                    dialog.manualInput = !dialog.manualInput
                    dialog.errorText = ""
                    if (dialog.manualInput) typedShortcut.forceActiveFocus()
                }
            }
        }
        ShortcutField {
            id: shortcut
            objectName: "customShortcutRecorder"
            Layout.fillWidth: true; implicitHeight: 42
            visible: !dialog.manualInput
            service: dialog.service
            sequence: dialog.capturedShortcut
            onRecorded: function(value) { dialog.capturedShortcut = value; dialog.errorText = "" }
            color: theme.text
            background: Rectangle { radius: 8; color: theme.surface; border.color: shortcut.activeFocus ? theme.primary : theme.line }
        }
        UiTextField {
            id: typedShortcut
            objectName: "customShortcutText"
            Layout.fillWidth: true; implicitHeight: 42; visible: dialog.manualInput
            hintText: "例如：Cmd+Shift+[ 或 Ctrl+Fn+Left"; maximumLength: 80; color: theme.text
            onTextEdited: dialog.errorText = ""
            background: Rectangle { radius: 8; color: theme.surface; border.color: typedShortcut.activeFocus ? theme.primary : theme.line }
        }
        Label {
            Layout.fillWidth: true
            text: shortcut.activeFocus ? "请按下组合键，Esc 取消录入" : "支持单组组合键；点击使用后立即生效并自动保存。"
            wrapMode: Text.Wrap; color: theme.muted; font.pixelSize: 11
        }
        UiNotice {
            Layout.fillWidth: true
            text: dialog.errorText || dialog.service.recordingError
            visible: text.length > 0; wrapMode: Text.Wrap; font.pixelSize: 12
        }
    }
    footer: DialogButtonBox {
        leftPadding: 24; rightPadding: 24; bottomPadding: 20; topPadding: 0; spacing: 10
        UiAction { objectName: "cancelCustomShortcutButton"; text: "取消"; DialogButtonBox.buttonRole: DialogButtonBox.RejectRole }
        UiAction {
            objectName: "useCustomShortcutButton"; text: "使用此快捷键"; primary: true
            enabled: actionName.text.trim().length > 0 && dialog.shortcutValue.length > 0
            DialogButtonBox.buttonRole: DialogButtonBox.ActionRole
            onClicked: {
                var action = dialog.service.catalog.customAction(actionName.text, dialog.shortcutValue)
                if (action.id) dialog.actionChosen(action)
                else dialog.errorText = dialog.service.catalog.message
            }
        }
        onRejected: dialog.close()
    }
}
