import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

FigmaDialog {
    id: dialog
    objectName: "gestureHistoryDialog"
    required property var controller
    parent: Overlay.overlay
    anchors.centerIn: parent
    title: "手势记录"
    width: Math.min(760, parent ? parent.width - 48 : 760)
    height: Math.min(620, parent ? parent.height - 48 : 620)
    modal: true
    focus: true
    z: 20
    popupType: Popup.Item
    FigmaTheme { id: theme }
    Connections {
        target: dialog.controller.gestureHistory
        function onOpenRequested() { dialog.open() }
    }
    contentItem: ColumnLayout {
        spacing: 12
        RowLayout {
            Layout.fillWidth: true
            Label { text: "场景与动作样本 · 最近 500 条"; color: theme.muted; Layout.fillWidth: true }
            FigmaAction { text: "刷新"; quiet: true; onClicked: dialog.controller.gestureHistory.refresh() }
            FigmaAction {
                objectName: "gestureHistoryExportButton"
                text: "筛选应用并导出"
                enabled: !dialog.controller.gestureHistory.busy
                onClicked: dialog.controller.gestureHistory.export()
            }
        }
        UiNotice { Layout.fillWidth: true; text: dialog.controller.gestureHistory.message; warning: dialog.controller.gestureHistory.error }
        ListView {
            id: list
            Layout.fillWidth: true; Layout.fillHeight: true
            clip: true; spacing: 10
            model: dialog.controller.gestureHistory.records
            ScrollBar.vertical: ScrollBar {}
            delegate: Rectangle {
                id: entry
                required property var modelData
                width: list.width; height: body.implicitHeight + 24
                color: theme.surface; border.color: theme.line; radius: 8
                ColumnLayout {
                    id: body
                    anchors.left: parent.left; anchors.right: parent.right
                    anchors.top: parent.top; anchors.margins: 12
                    spacing: 8
                    RowLayout {
                        Layout.fillWidth: true
                        Label { text: entry.modelData.gestureLabel; color: theme.text; font.bold: true }
                        Label { Layout.fillWidth: true; elide: Text.ElideRight; text: entry.modelData.applicationLabel + (entry.modelData.website ? " · " + entry.modelData.website : ""); color: theme.text }
                        Label { text: entry.modelData.created_at.slice(0, 19).replace("T", " "); color: theme.muted; font.pixelSize: 12 }
                    }
                    Label {
                        Layout.fillWidth: true; wrapMode: Text.Wrap; color: theme.muted
                        text: entry.modelData.sceneLabel + " · " + ({text: "输入框", nontext: "非输入框", unknown: "焦点未知"}[entry.modelData.focus] || "焦点未知")
                            + (entry.modelData.voice_state ? " · " + ({idle: "空闲", listening: "录音中", editing: "编辑中", applying: "写入中", dictated: "听写完成", edited: "编辑完成"}[entry.modelData.voice_state] || entry.modelData.voice_state) : "")
                            + " → " + (entry.modelData.action || "未选择动作") + " · " + entry.modelData.statusLabel
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        FigmaSelect {
                            id: feedback
                            model: ["未评价", "符合预期", "误触", "动作不对"]
                            currentIndex: ["", "correct", "accidental", "wrong_action"].indexOf(entry.modelData.feedback)
                            onActivated: dialog.controller.gestureHistory.annotate(entry.modelData.record_id, ["", "correct", "accidental", "wrong_action"][currentIndex], entry.modelData.expected_action)
                        }
                        TextField {
                            id: expected
                            Layout.fillWidth: true
                            visible: entry.modelData.feedback === "wrong_action"
                            placeholderText: "期望动作（可选）"; text: entry.modelData.expected_action
                            maximumLength: 256
                            onEditingFinished: if (text !== entry.modelData.expected_action) dialog.controller.gestureHistory.annotate(entry.modelData.record_id, "wrong_action", text)
                        }
                        Item { Layout.fillWidth: true; visible: !expected.visible }
                        FigmaAction { text: "删除"; quiet: true; onClicked: dialog.controller.gestureHistory.deleteRecord(entry.modelData.record_id) }
                    }
                }
            }
            Label { anchors.centerIn: parent; visible: list.count === 0; text: "还没有手势记录，连接 Ring 使用手势后会自动记录"; color: theme.muted }
        }
    }
}
