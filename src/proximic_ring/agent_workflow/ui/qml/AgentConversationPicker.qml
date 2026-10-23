import QtQuick
import QtQuick.Controls.Basic as Basic
import QtQuick.Controls
import QtQuick.Layouts

Basic.Popup {
    id: pickerView
    readonly property var picker: agent.conversationPicker
    property real maximumHeight: 218
    width: 256
    height: Math.min(implicitHeight, maximumHeight)
    padding: 6
    popupType: Popup.Item
    // Consume the dismissing click so it cannot send the visible task's draft.
    modal: true
    dim: false
    focus: true
    closePolicy: picker.connecting ? Popup.NoAutoClose : Popup.CloseOnEscape | Popup.CloseOnPressOutside
    onOpened: {
        conversations.currentIndex = conversations.count ? 0 : -1
        conversations.forceActiveFocus()
    }
    onClosed: picker.close()
    background: Rectangle {
        radius: 12
        color: "#343638"
        border.width: 1; border.color: "#575A60"
    }
    contentItem: ColumnLayout {
        spacing: 2
        Label {
            Layout.fillWidth: true
            Layout.leftMargin: 10; Layout.rightMargin: 10
            Layout.preferredHeight: 22
            text: pickerView.picker.connecting ? "正在连接…" : "最近对话"
            verticalAlignment: Text.AlignVCenter
            color: "#A8ABB0"; font.pixelSize: 11
        }
        Label {
            Layout.fillWidth: true
            Layout.margins: 10
            visible: pickerView.picker.loading || !!pickerView.picker.error || !conversations.count
            text: pickerView.picker.loading ? "正在读取…"
                : pickerView.picker.error || "暂无最近对话"
            color: pickerView.picker.error ? "#E2C28E" : "#AAACAF"
            font.pixelSize: 12; wrapMode: Text.Wrap; textFormat: Text.PlainText
        }
        ListView {
            id: conversations
            objectName: "agentConversationList"
            Layout.fillWidth: true
            Layout.fillHeight: true
            Layout.preferredHeight: contentHeight
            Layout.minimumHeight: 0
            visible: !pickerView.picker.loading && !pickerView.picker.error && count > 0
            clip: true; spacing: 2
            boundsBehavior: Flickable.StopAtBounds
            model: pickerView.picker.rows
            keyNavigationEnabled: !pickerView.picker.connecting
            keyNavigationWraps: true
            onCountChanged: if (count > 0 && currentIndex < 0) currentIndex = 0
            Keys.onReturnPressed: if (currentItem && currentItem.enabled) pickerView.picker.select(currentItem.modelData.id)
            Keys.onEnterPressed: if (currentItem && currentItem.enabled) pickerView.picker.select(currentItem.modelData.id)
            ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }
            delegate: Basic.Button {
                id: option
                required property var modelData
                required property int index
                objectName: "agentConversation_" + modelData.id
                width: conversations.width; height: 28
                enabled: !pickerView.picker.connecting && agent.canAddTask
                padding: 0; leftPadding: 10; rightPadding: 10
                focusPolicy: Qt.NoFocus
                contentItem: Text {
                    text: option.modelData.title
                    color: option.enabled ? "#E4E5E6" : "#929599"; font.pixelSize: 12
                    verticalAlignment: Text.AlignVCenter
                    elide: Text.ElideRight; textFormat: Text.PlainText
                }
                background: Rectangle {
                    radius: 6
                    color: option.hovered || option.ListView.isCurrentItem ? "#4B4E53" : "transparent"
                }
                onHoveredChanged: if (hovered) conversations.currentIndex = index
                Accessible.name: "连接到对话：" + modelData.title
                onClicked: pickerView.picker.select(modelData.id)
            }
        }
        Basic.Button {
            objectName: "agentConversationRetry"
            Layout.fillWidth: true
            visible: !!pickerView.picker.error && !pickerView.picker.loading
            implicitHeight: 32
            text: "重试"
            contentItem: Text {
                text: parent.text; color: "#E4E5E6"; font.pixelSize: 12
                horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter
            }
            background: Rectangle { radius: 6; color: parent.hovered ? "#4B4E53" : "transparent" }
            onClicked: pickerView.picker.refresh()
        }
    }
}
