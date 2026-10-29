import QtQuick
import QtQuick.Controls
import QtQuick.Controls.Basic as Basic
import QtQuick.Layouts

ColumnLayout {
    id: card
    required property var controller
    readonly property var history: controller.gestureMappingHistory
    readonly property var gestures: controller.voiceGestureOptions
    // Read the existing presentation adapter so combined start/end bindings appear once.
    readonly property var assignedActions: {
        var bindings = controller.gestureBindings
        var result = {}
        gestures.forEach(function(gesture) { result[gesture.key] = controller.voiceGestureAction(gesture.key) })
        return result
    }
    property string feedback: ""
    objectName: "voiceGestureSettingsCard"
    spacing: 16
    FigmaTheme { id: theme }
    Label {
        Layout.fillWidth: true
        text: "每项语音功能只能配置一个手势。修改后自动保存，对所有应用生效。"
        color: theme.muted; font.pixelSize: 14; wrapMode: Text.Wrap
    }
    UiScrollView {
        id: scroll
        objectName: "voiceGestureBindingsScroll"
        Layout.fillWidth: true; Layout.fillHeight: true
        contentWidth: availableWidth
        clip: true
        ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
        ColumnLayout {
            width: scroll.availableWidth
            spacing: 8
            Repeater {
                model: card.controller.voiceGestureActions
                Rectangle {
                    id: row
                    required property var modelData
                    readonly property var selectedGestures: card.gestures.filter(function(gesture) {
                        return card.assignedActions[gesture.key].id === row.modelData.id
                    })
                    readonly property string gestureText: selectedGestures.length ? selectedGestures.map(function(gesture) { return gesture.title }).join("、") : "无"
                    objectName: "voiceFunctionRow_" + modelData.voiceAction
                    Layout.fillWidth: true; Layout.minimumWidth: 0
                    Layout.preferredHeight: 56
                    radius: 10; color: theme.background; border.color: theme.line
                    RowLayout {
                        anchors.fill: parent; anchors.leftMargin: 16; anchors.rightMargin: 12
                        anchors.topMargin: 8; anchors.bottomMargin: 8; spacing: 20
                        Label {
                            text: row.modelData.label
                            Layout.preferredWidth: 144
                            font.family: theme.family; font.pixelSize: 14; color: theme.text
                        }
                        FigmaAction {
                            id: selection
                            objectName: "voiceFunctionGestures_" + row.modelData.voiceAction
                            Layout.fillWidth: true; Layout.minimumWidth: 200
                            text: row.gestureText
                            Accessible.name: row.modelData.label + "的手势：" + row.gestureText
                            leftPadding: 10; rightPadding: 12
                            contentItem: RowLayout {
                                spacing: 8
                                VoiceGesturePreview {
                                    visible: row.selectedGestures.length > 0
                                    gestures: row.selectedGestures.map(function(gesture) { return gesture.key })
                                    Layout.preferredWidth: 35
                                    Layout.preferredHeight: 32
                                }
                                Label {
                                    Layout.fillWidth: true
                                    text: row.gestureText; elide: Text.ElideRight
                                    font: selection.font; color: theme.text
                                }
                                FigmaIcon { symbol: "chevron-down"; Layout.preferredWidth: 16; Layout.preferredHeight: 16 }
                            }
                            onClicked: gestureMenu.open()
                        }
                    }
                    Basic.Popup {
                        id: gestureMenu
                        objectName: "voiceFunctionGestureMenu_" + row.modelData.voiceAction
                        parent: selection
                        popupType: Popup.Item
                        width: selection.width
                        height: menuContents.implicitHeight + padding * 2
                        x: 0; y: selection.height + 4
                        margins: 12
                        padding: 8; focus: true
                        closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
                        background: Rectangle { color: theme.background; radius: 8; border.color: theme.line }
                        contentItem: ColumnLayout {
                            id: menuContents
                            spacing: 8
                            Label {
                                Layout.fillWidth: true; Layout.margins: 4
                                text: row.modelData.label + " · 选择手势"; font.pixelSize: 13; color: theme.muted
                            }
                            GridLayout {
                                Layout.fillWidth: true
                                columns: 2; rowSpacing: 4; columnSpacing: 4
                                Repeater {
                                    model: gestureMenu.visible ? [{key:"", title:"无"}].concat(card.gestures) : []
                                    Basic.ItemDelegate {
                                        id: option
                                        required property var modelData
                                        readonly property var savedAction: card.assignedActions[modelData.key] || {}
                                        readonly property bool selected: modelData.key ? savedAction.id === row.modelData.id : row.selectedGestures.length === 0
                                        objectName: "voiceFunctionGesture_" + row.modelData.voiceAction + "_" + modelData.key
                                        Layout.columnSpan: modelData.key ? 1 : 2
                                        Layout.fillWidth: true; Layout.minimumWidth: 0
                                        Layout.preferredHeight: 48
                                        padding: 6; hoverEnabled: true
                                        text: modelData.title
                                        Accessible.role: Accessible.RadioButton
                                        Accessible.name: text
                                        Accessible.checkable: true
                                        Accessible.checked: selected
                                        contentItem: RowLayout {
                                            spacing: 8
                                            Rectangle {
                                                Layout.preferredWidth: 16; Layout.preferredHeight: 16
                                                radius: 8; color: theme.background
                                                border.color: option.selected ? theme.primary : theme.line
                                                Rectangle { anchors.centerIn: parent; width: 8; height: 8; radius: 4; visible: option.selected; color: theme.primary }
                                            }
                                            VoiceGesturePreview {
                                                visible: option.modelData.key.length > 0
                                                gestures: [option.modelData.key]
                                                Layout.preferredWidth: 34; Layout.preferredHeight: 32
                                            }
                                            ColumnLayout {
                                                Layout.fillWidth: true; spacing: 1
                                                Label { Layout.fillWidth: true; text: option.text; font.pixelSize: 13; color: theme.text; elide: Text.ElideRight }
                                                Label {
                                                    Layout.fillWidth: true
                                                    visible: !option.selected && !!option.savedAction.id
                                                    text: option.savedAction.label ? "当前：" + option.savedAction.label : ""
                                                    font.pixelSize: 11; color: theme.muted; elide: Text.ElideRight
                                                }
                                            }
                                        }
                                        background: Rectangle {
                                            radius: 6
                                            color: option.selected ? theme.selection : option.hovered ? theme.subtle : theme.background
                                            border.width: option.visualFocus ? 2 : 0; border.color: theme.primary
                                        }
                                        onClicked: {
                                            var gesture = modelData.key || (row.selectedGestures[0] || {}).key
                                            var action = modelData.key ? row.modelData : {id:"", voiceAction:""}
                                            var success = !gesture || card.history.apply("voice", "", "regular", gesture, action)
                                            card.feedback = success ? "" : card.history.error || "保存失败，请重试"
                                            if (success) gestureMenu.close()
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
    }
    Label {
        Layout.fillWidth: true; visible: card.feedback.length > 0
        text: card.feedback; color: theme.warning; font.pixelSize: 14; wrapMode: Text.Wrap
    }
    RowLayout {
        Layout.fillWidth: true
        Label { Layout.fillWidth: true; text: "自动保存"; color: theme.muted; font.pixelSize: 14 }
        FigmaAction {
            objectName: "undoVoiceGestureMappingButton"
            text: "撤销上一步"
            enabled: card.history.canUndo && card.history.contextKey.indexOf("voice/") === 0
            onClicked: {
                if (!card.history.undo(card.history.contextKey)) card.feedback = card.history.error || "无法撤销此修改"
                else card.feedback = ""
            }
        }
    }
}
