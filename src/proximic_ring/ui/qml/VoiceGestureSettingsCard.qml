import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Rectangle {
    id: card
    required property var controller
    FigmaTheme { id: theme }
    implicitHeight: content.implicitHeight + 44
    radius: 12; color: theme.surface; border.color: theme.line
    ColumnLayout {
        id: content
        anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
        anchors.margins: 22; spacing: 14
        RowLayout {
            Layout.fillWidth: true
            Label { Layout.fillWidth: true; text: "输入框内的手势"; color: theme.text; font.pixelSize: 20; font.weight: Font.DemiBold }
            FigmaAction {
                objectName: "voicePageResetGesturesButton"
                text: "恢复默认"; quiet: true
                onClicked: card.controller.resetGestureBindings()
            }
        }
        Rectangle {
            Layout.fillWidth: true
            implicitHeight: priorityContent.implicitHeight + 24
            radius: 10; color: theme.subtle
            ColumnLayout {
                id: priorityContent
                anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
                anchors.margins: 12; spacing: 5
                Label {
                    Layout.fillWidth: true
                    text: "同一手势冲突时，输入功能优先"
                    color: theme.primary; font.pixelSize: 13; font.weight: Font.DemiBold
                }
                Label {
                    objectName: "voicePageGesturePriorityNotice"
                    Layout.fillWidth: true
                    text: "输入框有焦点时，仅下列已绑定的手势优先执行输入功能；其他手势仍按应用配置优先、全局配置兜底执行，无需输入框失焦。语音操作进行中仍由本次输入接管，结束后恢复。"
                    color: theme.muted; font.pixelSize: 12; wrapMode: Text.Wrap
                }
            }
        }
        Label {
            Layout.fillWidth: true
            text: "以下显示当前手势，修改会自动保存。可自定义的功能最多设置两个手势，上滑发送和下滑润色为固定手势。"
            color: theme.muted; font.pixelSize: 12; wrapMode: Text.Wrap
        }
        GridLayout {
            Layout.fillWidth: true
            columns: width >= 840 ? 3 : width >= 560 ? 2 : 1
            rowSpacing: 10; columnSpacing: 12
            Repeater {
                model: [
                    {action: "confirm", title: "开始语音", detail: "开始本次听写，未聚焦时也可启动", icon: ""},
                    {action: "end", title: "结束语音", detail: "定稿当前一轮听写", icon: ""},
                    {action: "undo", title: "取消／撤销", detail: "处理中取消，完成后撤销", icon: "function-cancel"},
                    {action: "switch_mode", title: "转换为编辑", detail: "下划线期间转为编辑指令", icon: "function-edit"},
                    {action: "send", title: "发送", detail: "录音中先定稿再发送；完成后发送", icon: "", fixedGesture: "swipe-up", gestureLabel: "上滑"},
                    {action: "polish", title: "润色本句", detail: "录音中先结束并润色；完成后润色本次听写，只写回、不发送", icon: "", fixedGesture: "swipe-down", gestureLabel: "下滑"}
                ]
                Rectangle {
                    id: entry
                    required property var modelData
                    readonly property bool fixedBinding: !!modelData.fixedGesture
                    objectName: "voicePage_" + modelData.action + "Card"
                    Layout.fillWidth: true; Layout.fillHeight: true; Layout.minimumWidth: 0
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
                                gestures: entry.fixedBinding ? [entry.modelData.fixedGesture] : card.controller.gestureBindings[entry.modelData.action]
                                functionAsset: entry.modelData.icon
                            }
                            ColumnLayout {
                                Layout.fillWidth: true; Layout.minimumWidth: 0; spacing: 3
                                Label { Layout.fillWidth: true; text: entry.modelData.title; color: theme.text; font.pixelSize: 14; font.weight: Font.DemiBold; wrapMode: Text.Wrap }
                                Label { Layout.fillWidth: true; text: entry.modelData.detail; color: theme.muted; font.pixelSize: 11; wrapMode: Text.Wrap }
                            }
                        }
                        RowLayout {
                            visible: !entry.fixedBinding
                            Layout.fillWidth: true; spacing: 8
                            Repeater {
                                model: entry.fixedBinding ? 0 : 2
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
                        Label {
                            objectName: "voicePage_" + entry.modelData.action + "FixedGesture"
                            visible: entry.fixedBinding
                            Layout.fillWidth: true
                            text: entry.fixedBinding ? entry.modelData.gestureLabel + " · 固定手势" : ""
                            color: theme.muted; font.pixelSize: 12
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
