import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Rectangle {
    id: card
    required property var controller
    UiTheme { id: theme }
    implicitHeight: content.implicitHeight + 44
    radius: 16; color: theme.surface; border.color: theme.line
    ColumnLayout {
        id: content
        anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
        anchors.margins: 22; spacing: 14
        RowLayout {
            Layout.fillWidth: true
            Label { Layout.fillWidth: true; text: "语音手势"; color: theme.text; font.pixelSize: 20; font.weight: Font.DemiBold }
            UiAction {
                objectName: "voicePageResetGesturesButton"
                text: "恢复默认"; quiet: true
                onClicked: card.controller.resetGestureBindings()
            }
        }
        Label {
            Layout.fillWidth: true
            text: "点入文本框后使用，修改会自动保存。每项最多设置两个手势。"
            color: theme.muted; font.pixelSize: 12; wrapMode: Text.Wrap
        }
        GridLayout {
            Layout.fillWidth: true
            columns: width >= 840 ? 3 : 1
            rowSpacing: 10; columnSpacing: 12
            Repeater {
                model: [
                    {action: "confirm", title: "开始／结束语音", detail: "控制当前一轮听写", icon: ""},
                    {action: "undo", title: "取消／撤销", detail: "处理中取消，完成后撤销", icon: "function-cancel"},
                    {action: "switch_mode", title: "转换为编辑", detail: "下划线期间转为编辑指令", icon: "function-edit"}
                ]
                Rectangle {
                    id: entry
                    required property var modelData
                    Layout.fillWidth: true; Layout.minimumWidth: 0
                    implicitHeight: entryContent.implicitHeight + 28
                    color: "#FAFBFF"; border.color: theme.line; radius: 10
                    ColumnLayout {
                        id: entryContent
                        anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
                        anchors.margins: 14; spacing: 10
                        RowLayout {
                            Layout.fillWidth: true; spacing: 12
                            VoiceGesturePreview {
                                objectName: "voicePage_" + entry.modelData.action + "Preview"
                                gestures: card.controller.gestureBindings[entry.modelData.action]
                                functionAsset: entry.modelData.icon
                            }
                            ColumnLayout {
                                Layout.fillWidth: true; Layout.minimumWidth: 0; spacing: 3
                                Label { Layout.fillWidth: true; text: entry.modelData.title; color: theme.text; font.pixelSize: 14; font.weight: Font.DemiBold; wrapMode: Text.Wrap }
                                Label { Layout.fillWidth: true; text: entry.modelData.detail; color: theme.muted; font.pixelSize: 11; wrapMode: Text.Wrap }
                            }
                        }
                        RowLayout {
                            Layout.fillWidth: true; spacing: 8
                            Repeater {
                                model: 2
                                ColumnLayout {
                                    required property int index
                                    Layout.fillWidth: true; Layout.minimumWidth: 0; spacing: 4
                                    Label { text: parent.index === 0 ? "主手势" : "备用手势"; color: theme.muted; font.pixelSize: 11 }
                                    VoiceGestureBindingSelector {
                                        controller: card.controller
                                        actionName: entry.modelData.action
                                        slotIndex: parent.index
                                        namePrefix: "voicePage_"
                                        Layout.preferredHeight: 38
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
        UiNotice {
            objectName: "voicePageGestureError"
            Layout.fillWidth: true
            visible: card.controller.gestureSettingsError.length > 0
            text: card.controller.gestureSettingsError
            font.pixelSize: 12; wrapMode: Text.Wrap
        }
    }
}
