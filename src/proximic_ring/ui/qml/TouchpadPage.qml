import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

ScrollView {
    id: page
    objectName: "touchpadPage"
    required property var controller
    readonly property var touchpad: controller.touchpad
    signal homeRequested()
    UiTheme { id: theme }
    clip: true
    contentWidth: availableWidth
    contentHeight: touchpad.inputMode === "stroke" ? availableHeight : content.height
    ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
    ScrollBar.vertical.policy: ScrollBar.AsNeeded

    Column {
        id: content
        width: page.availableWidth
        spacing: 18
        Rectangle {
            width: parent.width; height: modeRow.implicitHeight + 36
            radius: 16
            color: theme.surface
            border.color: theme.line
            RowLayout {
                id: modeRow
                anchors.fill: parent; anchors.margins: 18; spacing: 12
                ColumnLayout {
                    Layout.fillWidth: true; spacing: 3
                    Label { text: "输入模式"; color: theme.text; font.pixelSize: 17; font.bold: true }
                    Label { text: "手动切换；运行中切换无需重新连接 Ring。"; color: theme.muted; font.pixelSize: 12 }
                }
                UiAction {
                    objectName: "pointerModeButton"
                    text: "光标控制"; primary: page.touchpad.inputMode === "pointer"
                    enabled: !page.touchpad.busy
                    onClicked: page.touchpad.setInputMode("pointer")
                }
                UiAction {
                    objectName: "strokeModeButton"
                    text: "笔画输入"; primary: page.touchpad.inputMode === "stroke"
                    enabled: !page.touchpad.busy
                    onClicked: page.touchpad.setInputMode("stroke")
                }
            }
        }
        Rectangle {
            visible: page.touchpad.inputMode !== "stroke"
            width: parent.width
            height: statusContent.implicitHeight + 44
            radius: 16; color: theme.surface; border.color: theme.line
            ColumnLayout {
                id: statusContent
                anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
                anchors.margins: 22; spacing: 16
                RowLayout {
                    Layout.fillWidth: true; spacing: 16
                    Rectangle {
                        width: 58; height: 58; radius: 16
                        color: page.touchpad.active ? "#EDF2FF" : theme.subtle
                        UiIcon { anchors.centerIn: parent; width: 29; height: 29; symbol: "pointer" }
                    }
                    ColumnLayout {
                        Layout.fillWidth: true; Layout.minimumWidth: 0; spacing: 6
                        Label {
                            objectName: "touchpadStatusTitle"
                            Layout.fillWidth: true
                            text: !page.touchpad.supported ? "输入模式支持 macOS 与 Windows"
                                : !page.controller.connected ? "请先在首页连接 Ring" : page.touchpad.title
                            color: theme.text; font.pixelSize: 20; font.weight: Font.DemiBold; wrapMode: Text.Wrap
                        }
                        Label {
                            objectName: "touchpadStatusDetail"
                            Layout.fillWidth: true
                            text: !page.controller.connected ? "连接后即可用 Ring 控制鼠标或输入笔画。"
                                : page.controller.recognitionEnabled && !page.touchpad.active ? "请先暂停语音识别，再开启触摸板。"
                                : page.controller.busy && !page.touchpad.active ? "设备正在准备，完成后即可开启。" : page.touchpad.message
                            color: page.touchpad.state === "error" ? "#B54756" : theme.muted
                            font.pixelSize: 12; wrapMode: Text.Wrap
                        }
                    }
                    UiAction {
                        objectName: "touchpadToggleButton"
                        Layout.preferredWidth: 142; Layout.preferredHeight: 44
                        visible: page.controller.connected
                        enabled: page.touchpad.active || (page.touchpad.available && !page.touchpad.busy)
                        text: page.touchpad.state === "starting" ? "取消开启"
                            : page.touchpad.state === "stopping" ? "正在停止…"
                            : page.touchpad.active ? "关闭输入" : "开启输入"
                        primary: !page.touchpad.active
                        onClicked: page.touchpad.toggle()
                    }
                    UiAction {
                        objectName: "touchpadHomeButton"
                        Layout.preferredWidth: 118; Layout.preferredHeight: 44
                        visible: !page.controller.connected
                        text: "返回首页"; primary: true
                        onClicked: page.homeRequested()
                    }
                }
                Rectangle { Layout.fillWidth: true; height: 1; color: theme.line }
                RowLayout {
                    Layout.fillWidth: true; spacing: 10
                    Rectangle { width: 7; height: 7; radius: 4; color: page.touchpad.state === "running" ? theme.success : "#9AA3B6" }
                    Label {
                        objectName: "touchpadCountdown"
                        Layout.fillWidth: true
                        text: page.touchpad.state === "running" ? (page.touchpad.inputMode === "stroke" ? "正在采集笔画 · " : "正在控制鼠标 · ") + page.touchpad.remaining + " 秒后自动停止"
                            : "开启后可随时点击关闭。光标模式也可按 Esc 停止。"
                        color: theme.muted; font.pixelSize: 12; wrapMode: Text.Wrap
                    }
                }
                Label {
                    Layout.fillWidth: true
                    text: "开启触摸板期间会暂时停止 Ring 麦克风和固件手势；关闭后自动恢复。"
                    color: theme.muted; font.pixelSize: 12; wrapMode: Text.Wrap
                }
                PermissionNotice {
                    objectName: "touchpadPermissionNotice"
                    Layout.fillWidth: true
                    message: Qt.platform.os === "osx" ? page.controller.inlineInput.permissions.accessibilityWarningText : ""
                    onActivated: page.controller.inlineInput.permissions.openSettings()
                }
            }
        }
        StrokeInputBox {
            width: parent.width
            height: Math.max(300, page.availableHeight - y)
            visible: page.touchpad.inputMode === "stroke"
            inputVisible: page.visible && visible
            controller: page.controller
            onHomeRequested: page.homeRequested()
        }
        Rectangle {
            visible: page.touchpad.inputMode === "pointer"
            width: parent.width
            height: settings.implicitHeight + 44
            radius: 16; color: theme.surface; border.color: theme.line
            ColumnLayout {
                id: settings
                anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
                anchors.margins: 22; spacing: 22
                ColumnLayout {
                    Layout.fillWidth: true; spacing: 5
                    Label { text: "触摸板设置"; color: theme.text; font.pixelSize: 20; font.weight: Font.DemiBold }
                    Label { Layout.fillWidth: true; text: "关闭触摸板后调整，下次开启时生效。"; color: theme.muted; font.pixelSize: 12; wrapMode: Text.Wrap }
                }
                SettingsFormRow {
                    title: "指针速度"
                    description: "根据你的操作习惯调整移动速度。"
                    RowLayout {
                        Layout.fillWidth: true
                        Slider {
                            objectName: "touchpadGainSlider"
                            Layout.fillWidth: true
                            from: 0.25; to: 3; stepSize: 0.05
                            value: page.touchpad.gain
                            enabled: !page.touchpad.active && !page.touchpad.busy
                            Accessible.name: "指针速度"
                            onMoved: page.touchpad.gain = value
                        }
                        Label { text: page.touchpad.gain.toFixed(2) + "×"; color: theme.text; font.pixelSize: 13 }
                    }
                }
                Rectangle { Layout.fillWidth: true; height: 1; color: theme.line }
                SettingsFormRow {
                    title: "轻触点击"
                    description: "识别短促轻触，执行鼠标左键单击。"
                    Switch {
                        objectName: "touchpadClicksSwitch"
                        Layout.alignment: Qt.AlignRight
                        checked: page.touchpad.clicks
                        enabled: !page.touchpad.active && !page.touchpad.busy
                        Accessible.name: "轻触点击"
                        onToggled: page.touchpad.clicks = checked
                    }
                }
                SettingsFormRow {
                    title: "反转上下方向"
                    description: "切换指针的垂直移动方向。"
                    Switch {
                        objectName: "touchpadInvertSwitch"
                        Layout.alignment: Qt.AlignRight
                        checked: page.touchpad.invertY
                        enabled: !page.touchpad.active && !page.touchpad.busy
                        Accessible.name: "反转上下方向"
                        onToggled: page.touchpad.invertY = checked
                    }
                }
                Rectangle { Layout.fillWidth: true; height: 1; color: theme.line }
                SettingsFormRow {
                    title: "自动停止"
                    description: "每次开启后，到达设定时间自动关闭。"
                    SettingsSelect {
                        objectName: "touchpadDurationSelect"
                        Layout.fillWidth: true; Layout.preferredHeight: 42
                        model: ["30 秒", "1 分 30 秒", "3 分钟", "5 分钟", "10 分钟"]
                        currentIndex: [30, 90, 180, 300, 600].indexOf(page.touchpad.seconds)
                        enabled: !page.touchpad.active && !page.touchpad.busy
                        Accessible.name: "自动停止时间"
                        onActivated: page.touchpad.seconds = [30, 90, 180, 300, 600][currentIndex]
                    }
                }
            }
        }
    }
}
