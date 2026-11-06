import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// §2.1/§2.8: writing first, status second, settings third; one primary action.
// §3.3/§3.6: stack at narrow widths, scroll vertically, keep ink square.
// Reuse FigmaTheme with 8/12/16/24 spacing, 12px radius, 13/14/18/22 type.
ScrollView {
    id: page
    objectName: "touchpadPage"
    required property var controller
    readonly property var touchpad: controller.touchpad
    readonly property bool windowActive: Window.window ? Window.window.active : false
    property string section: "stroke"
    readonly property bool narrow: availableWidth < 600
    signal homeRequested()
    FigmaTheme { id: theme }
    clip: true
    contentWidth: availableWidth
    contentHeight: viewport.contentHeight
    ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
    ScrollBar.vertical.policy: ScrollBar.AsNeeded
    contentItem: Flickable {
        id: viewport
        objectName: "touchpadViewport"
        clip: true
        boundsBehavior: Flickable.StopAtBounds
        boundsMovement: Flickable.StopAtBounds
        contentWidth: width
        contentHeight: content.implicitHeight + 24
        Column {
            id: content
            width: viewport.width
            spacing: 24
            Rectangle {
                width: parent.width; height: statusContent.implicitHeight + 32
                radius: 12; color: theme.surface; border.color: theme.line
                ColumnLayout {
                    id: statusContent
                    anchors { left: parent.left; right: parent.right; top: parent.top; margins: 16 }
                    spacing: 12
                    GridLayout {
                        Layout.fillWidth: true; columns: page.narrow ? 1 : 2
                        columnSpacing: 24; rowSpacing: 12
                        ColumnLayout {
                            Layout.fillWidth: true; Layout.minimumWidth: 0; spacing: 8
                            Label {
                                objectName: "touchpadStatusTitle"
                                Layout.fillWidth: true; wrapMode: Text.Wrap
                                text: !page.touchpad.supported ? "触摸板支持 Windows 和 macOS"
                                    : !page.controller.connected ? "连接 Ring，开始平面触控"
                                    : page.touchpad.strokePreparing ? "正在准备笔画输入"
                                    : page.touchpad.active ? (page.touchpad.inputMode === "stroke" ? "正在书写" : "正在控制鼠标") : "光标控制与笔画输入"
                                font.pixelSize: 22; font.weight: Font.Medium; color: theme.text
                            }
                            Label {
                                objectName: "touchpadStatusDetail"
                                Layout.fillWidth: true; wrapMode: Text.Wrap
                                text: !page.controller.connected ? "在首页连接后，即可移动光标或用笔画输入文字。" : page.touchpad.message
                                font.pixelSize: 13; color: page.touchpad.state === "error" ? theme.warning : theme.muted
                            }
                        }
                        FigmaAction {
                            objectName: "touchpadToggleButton"
                            visible: page.controller.connected
                            Layout.alignment: Qt.AlignLeft | Qt.AlignVCenter
                            text: page.touchpad.state === "starting" ? "取消开启" : page.touchpad.state === "stopping" ? "正在停止…" : page.touchpad.active ? "停止触摸板" : "开启鼠标控制"
                            primary: page.section === "pointer" && !page.touchpad.active
                            enabled: page.touchpad.active || (page.touchpad.available && !page.touchpad.busy)
                            onClicked: page.touchpad.toggle()
                        }
                        FigmaAction {
                            objectName: "touchpadHomeButton"
                            visible: !page.controller.connected; text: "连接 Ring"; primary: true
                            onClicked: page.homeRequested()
                        }
                    }
                    Rectangle { Layout.fillWidth: true; height: 1; color: theme.line }
                    Label {
                        objectName: "touchpadRunStatus"
                        Layout.fillWidth: true; wrapMode: Text.Wrap
                        text: page.touchpad.active ? "已启用 · " + (page.touchpad.inputMode === "stroke" ? "正在书写笔画" : "正在控制鼠标 · 可同时使用 Ring 或电脑麦克风") : "开启后可控制鼠标，同时使用 Ring 或电脑麦克风。"
                        color: theme.muted; font.pixelSize: 13
                    }
                    PermissionNotice {
                        objectName: "touchpadPermissionNotice"
                        Layout.fillWidth: true
                        message: page.touchpad.macPermissionsRequired ? page.controller.inlineInput.permissions.accessibilityWarningText : ""
                        onActivated: page.controller.inlineInput.permissions.openSettings()
                    }
                }
            }
            RowLayout {
                width: parent.width; spacing: 8
                FigmaAction {
                    objectName: "strokeModeButton"; text: "笔画输入"; quiet: true
                    contentColor: page.section === "stroke" ? theme.primary : theme.muted
                    font.weight: page.section === "stroke" ? Font.Medium : Font.Normal
                    onClicked: page.section = "stroke"
                }
                FigmaAction {
                    objectName: "pointerModeButton"; text: "鼠标设置"; quiet: true
                    contentColor: page.section === "pointer" ? theme.primary : theme.muted
                    onClicked: page.section = "pointer"
                }
                Item { Layout.fillWidth: true }
            }
            StrokeInputBox {
                width: parent.width; height: implicitHeight
                visible: page.section === "stroke"
                inputVisible: visible && page.visible && page.windowActive
                controller: page.controller
                onHomeRequested: page.homeRequested()
            }
            Rectangle {
                visible: page.section === "pointer"
                width: parent.width; height: settings.implicitHeight + 48
                radius: 12; color: theme.surface; border.color: theme.line
                ColumnLayout {
                    id: settings
                    anchors { left: parent.left; right: parent.right; top: parent.top; margins: 24 }
                    spacing: 16
                    Label { text: "鼠标设置"; color: theme.text; font.pixelSize: 18; font.weight: Font.Medium }
                    Label { Layout.fillWidth: true; text: "以下功能仅在触摸板开启时生效。功能开关可随时调整，指针速度和方向需停止后调整。"; color: theme.muted; font.pixelSize: 13; wrapMode: Text.Wrap }
                    Label { text: "指针速度"; color: theme.text; font.pixelSize: 14 }
                    RowLayout {
                        Layout.fillWidth: true; spacing: 16
                        Slider {
                            objectName: "touchpadGainSlider"
                            Layout.fillWidth: true; Layout.minimumWidth: 0
                            from: 0.25; to: 3; stepSize: 0.05; value: page.touchpad.gain
                            enabled: !page.touchpad.active && !page.touchpad.busy
                            Accessible.name: "指针速度"
                            onMoved: page.touchpad.gain = value
                        }
                        Label { text: page.touchpad.gain.toFixed(2) + "×"; color: theme.text }
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        Label { Layout.fillWidth: true; text: "左键单击"; color: theme.text; font.pixelSize: 14 }
                        FigmaSwitch { objectName: "touchpadClicksSwitch"; checked: page.touchpad.clicks; enabled: !page.touchpad.busy && (page.touchpad.raw2Supported || !page.touchpad.active); Accessible.name: "左键单击"; onToggled: page.touchpad.clicks = checked }
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        Label { Layout.fillWidth: true; text: "左键双击"; color: theme.text; font.pixelSize: 14 }
                        FigmaSwitch { objectName: "touchpadDoubleClickSwitch"; checked: page.touchpad.doubleClick; enabled: page.touchpad.raw2Supported && !page.touchpad.busy; Accessible.name: "左键双击"; onToggled: page.touchpad.doubleClick = checked }
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        Label { Layout.fillWidth: true; text: "右键单击"; color: theme.text; font.pixelSize: 14 }
                        FigmaSwitch { objectName: "touchpadRightClickSwitch"; checked: page.touchpad.rightClick; enabled: page.touchpad.raw2Supported && !page.touchpad.busy; Accessible.name: "右键单击"; onToggled: page.touchpad.rightClick = checked }
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        Label { Layout.fillWidth: true; text: "双指滚轮翻页"; color: theme.text; font.pixelSize: 14 }
                        FigmaSwitch { objectName: "touchpadScrollSwitch"; checked: page.touchpad.scroll; enabled: page.touchpad.raw2Supported && !page.touchpad.busy; Accessible.name: "双指滚轮翻页"; onToggled: page.touchpad.scroll = checked }
                    }
                    RowLayout {
                        Layout.fillWidth: true; spacing: 16
                        Label { text: "滚动速度"; color: theme.text; font.pixelSize: 14 }
                        Slider {
                            objectName: "touchpadScrollGainSlider"
                            Layout.fillWidth: true; Layout.minimumWidth: 0
                            from: 0.25; to: 3; stepSize: 0.05; value: page.touchpad.scrollGain
                            enabled: page.touchpad.raw2Supported && page.touchpad.scroll && !page.touchpad.busy
                            Accessible.name: "滚动速度"; onMoved: page.touchpad.scrollGain = value
                        }
                        Label { text: page.touchpad.scrollGain.toFixed(2) + "×"; color: theme.text }
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        Label { Layout.fillWidth: true; text: "反转指针上下方向"; color: theme.text; font.pixelSize: 14 }
                        FigmaSwitch { objectName: "touchpadInvertSwitch"; checked: page.touchpad.invertY; enabled: !page.touchpad.active && !page.touchpad.busy; Accessible.name: "反转指针上下方向"; onToggled: page.touchpad.invertY = checked }
                    }
                    Label {
                        Layout.fillWidth: true; wrapMode: Text.Wrap; font.pixelSize: 13; color: theme.muted
                        text: page.touchpad.raw2Supported ? "双击执行系统鼠标双击；笔画输入请使用书写区的「开始试写」。连接重置时自动清空旧操作，断流时停止输出，重复和过期点击自动忽略。需要配套新版触摸板固件。" : "当前平台使用兼容触摸板，暂不支持新版双指滚动和独立点击设置。笔画输入可在书写区使用。"
                    }
                }
            }
        }
    }
}
