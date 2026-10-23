import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

UiScrollView {
    id: page
    objectName: "touchpadPage"
    required property var controller
    readonly property var touchpad: controller.touchpad
    readonly property bool windowActive: Window.window ? Window.window.active : false
    property string section: "stroke"
    signal homeRequested()
    FigmaTheme { id: theme }
    clip: true; contentWidth: availableWidth; contentHeight: content.height
    ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
    Column {
        id: content
        width: page.availableWidth; spacing: 20
        Rectangle {
            width: parent.width; height: statusContent.implicitHeight + 40
            radius: 12; color: theme.surface; border.color: theme.line
            ColumnLayout {
                id: statusContent
                anchors { left: parent.left; right: parent.right; top: parent.top; margins: 20 }
                spacing: 16
                RowLayout {
                    Layout.fillWidth: true; spacing: 16
                    Label { text: "光标控制与笔画输入"; color: theme.text; font.pixelSize: 22; font.weight: Font.Medium }
                    FigmaEnableControl {
                        objectName: "touchpadToggleButton"
                        text: page.touchpad.active ? "停止触摸板" : "开启触摸板"
                        checked: page.touchpad.active
                        enabled: page.touchpad.active || (page.touchpad.available && !page.touchpad.busy)
                        Accessible.name: page.touchpad.active ? "停止触摸板" : "开启触摸板"
                        onClicked: page.touchpad.toggle()
                    }
                    Item { Layout.fillWidth: true }
                    FigmaAction { objectName: "touchpadHomeButton"; visible: !page.controller.connected; text: "返回首页"; onClicked: page.homeRequested() }
                }
                RowLayout {
                    Layout.fillWidth: true; spacing: 12
                    FigmaIcon { objectName: "touchpadDoubleTapIcon"; visible: page.touchpad.active; symbol: "tap" }
                    Label { objectName: "touchpadDoubleTapLabel"; visible: page.touchpad.active; text: "双击"; font.pixelSize: 16; font.weight: Font.Medium; color: theme.text }
                    Label { objectName: "touchpadRunStatus"; Layout.fillWidth: true; text: page.touchpad.active ? (page.touchpad.inputMode === "stroke" ? "笔画输入中 · 双击回到鼠标" : "已启用 · 在输入框内双击进入笔画") : "请启用功能以体验鼠标控制和笔画输入"; font.pixelSize: 16; color: theme.muted; wrapMode: Text.Wrap }
                }
                Label { objectName: "touchpadStatusTitle"; visible: !page.controller.connected || !page.touchpad.supported; text: !page.touchpad.supported ? "触摸板支持 macOS" : "请先在首页连接 Ring"; color: theme.muted; font.pixelSize: 14 }
                Label { objectName: "touchpadStatusDetail"; Layout.fillWidth: true; visible: page.touchpad.state === "error" || page.touchpad.busy; text: page.touchpad.message; color: theme.warning; font.pixelSize: 14; wrapMode: Text.Wrap }
                PermissionNotice { objectName: "touchpadPermissionNotice"; Layout.fillWidth: true; message: page.touchpad.supported ? (page.controller.inlineInput.permissions.accessibilityWarningText ? page.controller.inlineInput.permissions.accessibilityWarningText + " 请在设置的“系统与权限”中管理。" : "") : ""; actionText: "" }
            }
        }
        GridLayout {
            width: parent.width; columns: width >= 840 ? 2 : 1; columnSpacing: 24; rowSpacing: 20
            Rectangle {
                Layout.alignment: Qt.AlignTop; Layout.fillWidth: true; Layout.preferredWidth: 328; Layout.minimumWidth: 290
                Layout.preferredHeight: settingsContent.implicitHeight + 40
                radius: 12; color: theme.surface; border.color: theme.line
                ColumnLayout {
                    id: settingsContent
                    anchors { left: parent.left; right: parent.right; top: parent.top; margins: 20 }
                    spacing: 16
                    RowLayout {
                        Layout.fillWidth: true
                        Label { Layout.fillWidth: true; Layout.minimumWidth: 0; text: "光标偏好调节"; color: theme.text; font.pixelSize: 22; font.weight: Font.Medium }
                        FigmaAction { text: "高级设置"; navigation: true; enabled: false; implicitWidth: 129 }
                    }
                    RowLayout {
                        Layout.fillWidth: true; spacing: 12
                        Label { text: "指针速度"; color: theme.text; font.pixelSize: 16 }
                        Image { source: "../assets/figma/redesign-speed-tortoise.svg" }
                        Slider {
                            id: gain
                            objectName: "touchpadGainSlider"
                            Layout.fillWidth: true
                            from: 0.25; to: 3; stepSize: 0.05
                            value: page.touchpad.gain
                            enabled: !page.touchpad.active && !page.touchpad.busy
                            Accessible.name: "指针速度"
                            onMoved: page.touchpad.gain = value
                            background: Rectangle { x: gain.leftPadding; y: (gain.height - height) / 2; width: gain.availableWidth; height: 4; radius: 2; color: theme.line; Rectangle { height: 4; radius: 2; width: gain.visualPosition * parent.width; color: theme.primary } }
                            handle: Rectangle { x: gain.leftPadding + gain.visualPosition * (gain.availableWidth - width); y: (gain.height - height) / 2; width: 20; height: 14; radius: 7; color: "white"; border.color: gain.activeFocus ? theme.primary : theme.line }
                        }
                        Image { source: "../assets/figma/redesign-speed-hare.svg" }
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        Label { Layout.fillWidth: true; text: "轻触点击"; color: theme.text; font.pixelSize: 16 }
                        FigmaSwitch { objectName: "touchpadClicksSwitch"; checked: page.touchpad.clicks; enabled: !page.touchpad.active && !page.touchpad.busy; Accessible.name: "轻触点击"; onToggled: page.touchpad.clicks = checked }
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        Label { Layout.fillWidth: true; text: "反转上下方向"; color: theme.text; font.pixelSize: 16 }
                        FigmaSwitch { objectName: "touchpadInvertSwitch"; checked: page.touchpad.invertY; enabled: !page.touchpad.active && !page.touchpad.busy; Accessible.name: "反转上下方向"; onToggled: page.touchpad.invertY = checked }
                    }
                    Label { Layout.fillWidth: true; visible: page.touchpad.active; text: "停止触摸板后可调整，下次开启时生效。"; color: theme.muted; font.pixelSize: 14; wrapMode: Text.Wrap }
                }
            }
            Rectangle {
                Layout.fillWidth: true; Layout.preferredWidth: 580; Layout.minimumWidth: 380
                Layout.preferredHeight: Math.max(390, practice.implicitHeight + 80)
                radius: 12; color: theme.surface; border.color: theme.line
                ColumnLayout {
                    anchors.fill: parent; anchors.margins: 20; spacing: 12
                    RowLayout {
                        Layout.fillWidth: true
                        Label { Layout.fillWidth: true; text: "输入模拟"; color: theme.text; font.pixelSize: 22; font.weight: Font.Medium }
                        FigmaAction { text: "功能教学"; navigation: true; enabled: false }
                    }
                    StrokeInputBox {
                        id: practice
                        Layout.fillWidth: true; Layout.fillHeight: true
                        compactPresentation: true
                        inputVisible: page.visible && page.windowActive
                        controller: page.controller
                        onHomeRequested: page.homeRequested()
                    }
                }
            }
        }
    }
}
