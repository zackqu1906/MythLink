import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

ScrollView {
    id: page
    required property var controller
    signal voiceRequested(bool history)
    signal gesturesRequested()
    signal helpRequested()
    signal connectionRequested()
    signal devicePickerRequested()
    UiTheme { id: theme }
    objectName: "homePage"
    clip: true
    contentWidth: availableWidth
    ScrollBar.horizontal.policy: ScrollBar.AlwaysOff

    ColumnLayout {
        width: page.availableWidth
        spacing: 24
        ColumnLayout {
            objectName: "homePermissionNotices"
            Layout.fillWidth: true; spacing: 2
            readonly property var notices: page.controller.inlineInput.permissions.essentialWarnings.filter(
                function(item) { return item.kind !== "microphone" || page.controller.audioSource === "microphone" })
            visible: notices.length > 0
            Repeater {
                model: parent.notices
                PermissionNotice {
                    required property var modelData
                    objectName: "homePermission_" + modelData.kind
                    Layout.fillWidth: true
                    message: modelData.text
                    onActivated: page.controller.inlineInput.permissions.openPermissionSettings(modelData.kind)
                }
            }
        }
        GridLayout {
            Layout.fillWidth: true
            columns: width >= 900 ? 2 : 1
            columnSpacing: 16; rowSpacing: 16
            Rectangle {
                Layout.fillWidth: true
                Layout.preferredWidth: 420
                Layout.preferredHeight: 252
                radius: 18; color: theme.surface
                ColumnLayout {
                    anchors.fill: parent; anchors.margins: 20; spacing: 6
                    RowLayout {
                        Layout.fillWidth: true
                        UiIcon { symbol: "bluetooth"; Layout.preferredWidth: 21; Layout.preferredHeight: 21 }
                        Label {
                            Layout.fillWidth: true
                            text: page.controller.deviceName || "我的 Ring"
                            color: theme.muted; font.pixelSize: 13; elide: Text.ElideRight
                        }
                        UiAction {
                            objectName: "homeConnectionButton"
                            text: page.controller.connected
                                  ? (page.controller.statusKind === "stopping" ? "正在断开…" : "断开设备")
                                  : page.controller.busy ? "正在连接…" : page.controller.scanBusy ? "正在搜索…"
                                  : page.controller.canReconnect ? "重新连接" : "蓝牙连接"
                            primary: !page.controller.connected
                            enabled: page.controller.connected ? page.controller.statusKind !== "stopping" : !page.controller.busy && !page.controller.scanBusy
                            onClicked: page.connectionRequested()
                        }
                        UiAction {
                            objectName: "homeDevicePickerButton"
                            text: "更换设备"
                            quiet: true
                            visible: page.controller.canReconnect || page.controller.connected
                            enabled: !page.controller.connected && !page.controller.busy && !page.controller.scanBusy
                            ToolTip.visible: hovered
                            ToolTip.text: page.controller.connected ? "请先断开当前设备，再选择其他戒指" : "搜索并选择其他戒指"
                            onClicked: page.devicePickerRequested()
                        }
                    }
                    RingIllustration { Layout.alignment: Qt.AlignHCenter; Layout.fillHeight: true; Layout.preferredWidth: 210 }
                    RowLayout {
                        Layout.alignment: Qt.AlignHCenter; spacing: 7
                        Rectangle { width: 6; height: 6; radius: 3; color: page.controller.connected ? theme.success : "#A0A8BA" }
                        Label {
                            text: page.controller.connected
                                ? "已连接" + (page.controller.batteryAvailable ? " · 电量 " + page.controller.batteryPercentage + "%" : "")
                                : page.controller.busy ? page.controller.statusTitle
                                : page.controller.scanBusy ? "正在搜索附近的蓝牙设备"
                                : "连接 Ring，开始语音与手势交互"
                            color: theme.muted; font.pixelSize: 12
                        }
                    }
                }
            }
            HomeStatistics {
                objectName: "homeStatisticsPanel"
                Layout.fillWidth: true; Layout.preferredWidth: 520
                statistics: page.controller.homeStatistics
            }
        }
        Rectangle {
            objectName: "homeConnectionError"
            Layout.fillWidth: true
            visible: !page.controller.connected && page.controller.statusKind === "error"
            implicitHeight: connectionError.implicitHeight + 28
            radius: 12; color: "#FFF3F1"; border.color: "#F0D9D5"
            ColumnLayout {
                id: connectionError
                anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top; anchors.margins: 14; spacing: 5
                Label { Layout.fillWidth: true; text: page.controller.statusTitle; color: "#AF4650"; font.pixelSize: 14; font.bold: true; wrapMode: Text.Wrap }
                Label { Layout.fillWidth: true; text: page.controller.statusDetail; color: theme.muted; font.pixelSize: 12; wrapMode: Text.Wrap }
            }
        }
        ColumnLayout {
            Layout.fillWidth: true; spacing: 12
            RowLayout {
                Layout.fillWidth: true
                Label { text: "快速开始"; color: theme.text; font.pixelSize: 20; font.weight: Font.DemiBold }
                Item { Layout.fillWidth: true }
                UiAction { text: "了解如何使用"; quiet: true; onClicked: page.helpRequested() }
            }
            GridLayout {
                Layout.fillWidth: true; columns: 3; columnSpacing: 12
                Repeater {
                    model: [{title: "语音输入", detail: "说话完成输入与修改", icon: "edit", link: "开始使用", target: "voice"},
                            {title: "场景与手势", detail: "查看不同场景中的手势", icon: "coffee", link: "查看场景规则", target: "gestures"},
                            {title: "手势控制", detail: "了解轻点、滑动与捏合", icon: "book", link: "查看使用说明", target: "help"}]
                    delegate: AbstractButton {
                        required property var modelData
                        Layout.fillWidth: true; Layout.preferredWidth: 280; implicitHeight: 130
                        hoverEnabled: true
                        Accessible.name: modelData.title
                        onClicked: modelData.target === "voice" ? page.voiceRequested(false) : modelData.target === "gestures" ? page.gesturesRequested() : page.helpRequested()
                        background: Rectangle { radius: 14; color: parent.hovered ? "#F0F3FF" : theme.surface; border.color: parent.activeFocus ? theme.primary : "transparent" }
                        contentItem: ColumnLayout {
                            anchors.fill: parent; anchors.margins: 18; spacing: 6
                            RowLayout {
                                Layout.fillWidth: true
                                Label { text: modelData.title; color: theme.text; font.pixelSize: 16; font.weight: Font.DemiBold; Layout.fillWidth: true }
                                UiIcon { symbol: modelData.icon; ink: theme.text; Layout.preferredWidth: 22; Layout.preferredHeight: 22 }
                            }
                            Label { Layout.fillWidth: true; text: modelData.detail; color: theme.muted; font.pixelSize: 12; wrapMode: Text.Wrap }
                            Item { Layout.fillHeight: true }
                            Label { text: modelData.link + " ↗"; color: theme.primary; font.pixelSize: 12 }
                        }
                    }
                }
            }
        }
        ColumnLayout {
            Layout.fillWidth: true; spacing: 12
            RowLayout {
                Layout.fillWidth: true
                Label { text: "语音输入历史"; color: theme.text; font.pixelSize: 20; font.weight: Font.DemiBold }
                Item { Layout.fillWidth: true }
                UiAction { objectName: "homeAllHistoryButton"; text: "查看全部 →"; quiet: true; onClicked: page.voiceRequested(true) }
            }
            Rectangle {
                Layout.fillWidth: true
                implicitHeight: Math.max(158, recentContent.implicitHeight + 36)
                color: theme.surface; radius: 16
                ColumnLayout {
                    id: recentContent
                    anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
                    anchors.margins: 18; spacing: 16
                    Repeater {
                        model: page.controller.voiceHistoryEntries.slice(0, 3)
                        delegate: VoiceHistoryEntry {
                            required property var modelData
                            Layout.fillWidth: true
                            entry: modelData
                            controller: page.controller
                            compact: true
                        }
                    }
                    Item {
                        visible: page.controller.voiceHistoryEntries.length === 0
                        Layout.fillWidth: true; implicitHeight: 108
                        ColumnLayout {
                            anchors.centerIn: parent; spacing: 8
                            UiIcon { Layout.alignment: Qt.AlignHCenter; symbol: "history"; ink: "#9AA5BD"; width: 28; height: 28 }
                            Label { Layout.alignment: Qt.AlignHCenter; text: "还没有语音记录"; color: theme.text; font.pixelSize: 14 }
                            Label { Layout.alignment: Qt.AlignHCenter; text: "完成一次语音输入后，记录会出现在这里"; color: theme.muted; font.pixelSize: 12 }
                        }
                    }
                }
            }
        }
        Item { Layout.preferredHeight: 6 }
    }
}
