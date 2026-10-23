import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

UiScrollView {
    id: page
    required property var controller
    signal voiceRequested(bool history)
    signal gesturesRequested()
    signal touchpadRequested()
    signal helpRequested()
    signal tutorialRequested()
    signal connectionRequested()
    signal deleteHistoryRequested(var record)
    signal devicePickerRequested()
    FigmaTheme { id: theme }
    objectName: "homePage"
    clip: true
    contentWidth: availableWidth
    ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
    ScrollBar.vertical.policy: ScrollBar.AlwaysOff
    Component.onCompleted: page.controller.inlineInput.refreshInputMethodVersion()
    onVisibleChanged: {
        if (visible && controller) controller.inlineInput.refreshInputMethodVersion()
    }

    ColumnLayout {
        width: page.availableWidth
        spacing: 24
        PermissionNotice {
            objectName: "homeInputMethodNotice"
            Layout.fillWidth: true
            readonly property string versionState: page.controller.inlineInput.inputMethodVersion.state
            visible: page.controller.inlineInput.enabled
                     && (versionState === "outdated" || versionState === "not_installed")
            message: page.controller.inlineInput.installing ? "正在更新语音输入法…"
                     : versionState === "not_installed" ? "语音输入法尚未安装，请在设置的“系统与权限”中安装。"
                     : "语音输入法待更新，请在设置的“系统与权限”中更新。"
            actionText: ""
            enabled: !page.controller.inlineInput.installing
        }
        ColumnLayout {
            objectName: "homePermissionNotices"
            Layout.fillWidth: true; spacing: 8
            readonly property var notices: page.controller.inlineInput.permissions.essentialWarnings.filter(
                function(item) { return item.kind !== "microphone" || page.controller.audioSource === "microphone" })
            visible: notices.length > 0
            Repeater {
                model: parent.notices
                PermissionNotice {
                    required property var modelData
                    objectName: "homePermission_" + modelData.kind
                    Layout.fillWidth: true
                    message: modelData.text + " 请在设置的“系统与权限”中管理。"
                    actionText: ""
                }
            }
        }
        GridLayout {
            Layout.fillWidth: true
            columns: width >= 850 ? 2 : 1
            columnSpacing: 16; rowSpacing: 16
            Rectangle {
                Layout.fillWidth: true
                Layout.preferredWidth: 418
                Layout.preferredHeight: 234
                radius: 16; color: theme.surface; border.color: theme.line
                ColumnLayout {
                    anchors.fill: parent; anchors.margins: 20; spacing: 6
                    RowLayout {
                        Layout.fillWidth: true
                        FigmaIcon { symbol: "ring"; Layout.preferredWidth: 20; Layout.preferredHeight: 20 }
                        Label {
                            Layout.fillWidth: true
                            text: page.controller.deviceName || "我的 Ring"
                            color: theme.text; font.pixelSize: 22; font.weight: Font.Bold; elide: Text.ElideRight
                        }
                        FigmaAction {
                            objectName: "homeConnectionButton"
                            text: page.controller.connected
                                  ? (page.controller.statusKind === "stopping" ? "正在断开…" : "断开设备")
                                  : page.controller.busy ? "正在连接…" : page.controller.scanBusy ? "正在搜索…"
                                  : page.controller.canReconnect ? "重新连接" : "蓝牙连接"
                            primary: !page.controller.connected
                            enabled: page.controller.connected ? page.controller.statusKind !== "stopping" : !page.controller.busy && !page.controller.scanBusy
                            onClicked: page.connectionRequested()
                        }
                        FigmaAction {
                            objectName: "homeDevicePickerButton"
                            text: "切换设备"
                            quiet: true
                            visible: page.controller.canReconnect || page.controller.connected
                            enabled: !page.controller.connected && !page.controller.busy && !page.controller.scanBusy
                            ToolTip.visible: hovered
                            ToolTip.text: page.controller.connected ? "请先断开当前设备，再选择其他戒指" : "搜索并选择其他戒指"
                            onClicked: page.devicePickerRequested()
                        }
                    }
                    Image { Layout.alignment: Qt.AlignHCenter; Layout.fillHeight: true; Layout.preferredWidth: 128; Layout.preferredHeight: 143; source: "../assets/figma/redesign-383-9247-ring-product-artwork.png"; fillMode: Image.PreserveAspectFit }
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
                Layout.fillWidth: true; Layout.preferredWidth: 498
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
                Label { text: "快速开始"; color: theme.text; font.pixelSize: 22; font.weight: Font.Medium }
                Item { Layout.fillWidth: true }

            }
            GridLayout {
                Layout.fillWidth: true; columns: width >= 760 ? 3 : 1; columnSpacing: 16; rowSpacing: 12
                Repeater {
                    model: [{title: "语音输入", detail: "学会听写、润色和修改", icon: "mic", link: "新手教学", target: "voice"},
                            {title: "手势控制", detail: "调整不同场景的手势映射", icon: "gesture", link: "配置手势", target: "gestures"},
                            {title: "触摸板与笔画输入", detail: "控制鼠标，使用笔画输入", icon: "pointer", link: "新手教学", target: "touchpad"}]
                    delegate: AbstractButton {
                        required property var modelData
                        Layout.fillWidth: true; Layout.preferredWidth: 280; implicitHeight: 144
                        hoverEnabled: true
                        Accessible.name: modelData.title
                        onClicked: { if (modelData.target === "gestures") page.gesturesRequested(); else if (modelData.target === "voice") page.tutorialRequested() }
                        background: Rectangle { radius: 12; color: theme.surface; border.color: theme.line }
                        contentItem: ColumnLayout {
                            anchors.fill: parent; anchors.margins: 16; spacing: 6
                            RowLayout {
                                Layout.fillWidth: true
                                Label { text: modelData.title; color: theme.text; font.pixelSize: 18; font.weight: Font.Medium; Layout.fillWidth: true; elide: Text.ElideRight }
                                Rectangle { Layout.preferredWidth: 40; Layout.preferredHeight: 40; color: theme.subtle; radius: 10; FigmaIcon { anchors.centerIn: parent; symbol: modelData.icon } }
                            }
                            Label { Layout.fillWidth: true; text: modelData.detail; color: theme.muted; font.pixelSize: 16; wrapMode: Text.Wrap }
                            Item { Layout.fillHeight: true }
                            FigmaAction { Layout.fillWidth: true; implicitHeight: 32; navigation: true; text: modelData.link; enabled: modelData.target !== "touchpad"; onClicked: { if (modelData.target === "voice") page.tutorialRequested(); else page.gesturesRequested() } ToolTip.visible: hovered && !enabled; ToolTip.text: "教学功能暂未开放" }
                        }
                    }
                }
            }
        }
        ColumnLayout {
            Layout.fillWidth: true; spacing: 12
            RowLayout {
                Layout.fillWidth: true
                Label { text: "最近语音记录"; color: theme.text; font.pixelSize: 22; font.weight: Font.Medium }
                Item { Layout.fillWidth: true }
                FigmaAction { objectName: "homeAllHistoryButton"; text: "查看全部记录"; navigation: true; implicitHeight: 32; onClicked: page.voiceRequested(true) }
            }
            Rectangle {
                Layout.fillWidth: true
                implicitHeight: recentHistory.count > 0 ? recentHistory.implicitHeight : 158
                color: recentHistory.count > 0 ? "transparent" : theme.surface
                radius: 12; border.color: theme.line; border.width: recentHistory.count > 0 ? 0 : 1
                ListView {
                    id: recentHistory
                    objectName: "homeVoiceHistoryList"
                    anchors.fill: parent
                    spacing: 12
                    model: page.controller.voiceHistoryEntries.slice(0, 10)
                    clip: true; boundsBehavior: Flickable.StopAtBounds
                    flickableDirection: Flickable.VerticalFlick
                    // Fit three complete rows, including variable-length text, while
                    // keeping the ten most recent records scrollable.
                    property var previewHeights: ({})
                    implicitHeight: {
                        let total = 0
                        for (let i = 0; i < Math.min(3, count); ++i)
                            total += previewHeights[i] || 144
                        return total + Math.max(0, Math.min(3, count) - 1) * spacing
                    }
                    function rememberHeight(index, rowHeight) {
                        if (index < 0 || index >= 3 || rowHeight <= 0 || previewHeights[index] === rowHeight) return
                        let updated = Object.assign({}, previewHeights)
                        updated[index] = rowHeight
                        previewHeights = updated
                    }
                    ScrollBar.vertical: PreferenceScrollBar { }
                    footer: Item {
                        width: recentHistory.width
                        height: recentHistory.count > 0 ? historyLimitHint.implicitHeight + 32 : 0
                        visible: recentHistory.count > 0
                        Label {
                            id: historyLimitHint
                            objectName: "homeHistoryLimitHint"
                            anchors.centerIn: parent
                            width: parent.width - 24
                            text: "仅显示最新 10 条记录，完整记录请在“历史记录”中查看。"
                            color: theme.muted; font.pixelSize: 14
                            wrapMode: Text.Wrap; horizontalAlignment: Text.AlignHCenter
                        }
                    }
                    delegate: VoiceHistoryEntry {
                        required property int index
                        required property var modelData
                        entry: modelData
                        objectName: "homeVoiceHistoryEntry" + index
                        width: recentHistory.width
                        controller: page.controller
                        compact: false
                        Component.onCompleted: recentHistory.rememberHeight(index, implicitHeight)
                        onImplicitHeightChanged: recentHistory.rememberHeight(index, implicitHeight)
                        onIndexChanged: recentHistory.rememberHeight(index, implicitHeight)
                        onDeleteRequested: function(record) { page.deleteHistoryRequested(record) }
                    }
                }
                Item {
                    visible: recentHistory.count === 0
                    anchors.fill: parent
                    ColumnLayout {
                        anchors.centerIn: parent; spacing: 8
                        UiIcon { Layout.alignment: Qt.AlignHCenter; symbol: "history"; ink: "#9AA5BD"; width: 28; height: 28 }
                        Label { Layout.alignment: Qt.AlignHCenter; text: "还没有语音记录"; color: theme.text; font.pixelSize: 14 }
                        Label { Layout.alignment: Qt.AlignHCenter; text: "完成一次语音输入后，记录会出现在这里"; color: theme.muted; font.pixelSize: 12 }
                    }
                }
            }
        }
        Item { Layout.preferredHeight: 6 }
    }
}
