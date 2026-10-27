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
                        text: page.touchpad.blocksVoice ? "已启用 · " + (page.touchpad.inputMode === "stroke" ? "正在书写笔画" : "正在控制鼠标") + " · 麦克风已暂停，停止后恢复原设置" : "开启触摸板时会暂停麦克风和语音识别。"
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
                    spacing: 24
                    Label { text: "鼠标设置"; color: theme.text; font.pixelSize: 18; font.weight: Font.Medium }
                    Label { Layout.fillWidth: true; text: "停止触摸板后调整，下次开启时生效。"; color: theme.muted; font.pixelSize: 13; wrapMode: Text.Wrap }
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
                        Label { Layout.fillWidth: true; text: "轻触点击"; color: theme.text; font.pixelSize: 14 }
                        FigmaSwitch { objectName: "touchpadClicksSwitch"; checked: page.touchpad.clicks; enabled: !page.touchpad.active && !page.touchpad.busy; Accessible.name: "轻触点击"; onToggled: page.touchpad.clicks = checked }
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        Label { Layout.fillWidth: true; text: "反转上下方向"; color: theme.text; font.pixelSize: 14 }
                        FigmaSwitch { objectName: "touchpadInvertSwitch"; checked: page.touchpad.invertY; enabled: !page.touchpad.active && !page.touchpad.busy; Accessible.name: "反转上下方向"; onToggled: page.touchpad.invertY = checked }
                    }
                    Label {
                        Layout.fillWidth: true; wrapMode: Text.Wrap; font.pixelSize: 13; color: theme.muted
                        text: page.touchpad.externalStrokeSupported ? "双击当前输入框进入笔画输入，再双击或离开输入框回到鼠标。外部输入需要启用 Mythlink 输入法。" : "支持移动、单击和双击；按 Esc 停止鼠标控制。笔画输入可在本程序的书写区使用。"
                    }
                }
            }
        }
    }
}
