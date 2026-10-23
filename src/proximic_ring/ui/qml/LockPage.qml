import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

UiScrollView {
    id: page
    required property var controller
    readonly property var proximity: controller.proximity
    readonly property bool hasCalibration: proximity.savedCalibration.baseline !== undefined
    signal settingsRequested()
    objectName: "lockPage"
    FigmaTheme { id: theme }
    clip: true; contentWidth: availableWidth; contentHeight: content.height
    ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
    onVisibleChanged: if (visible) proximity.refresh()
    Column {
        id: content
        width: page.availableWidth; spacing: 20
        Rectangle {
            width: parent.width; height: enabledContent.implicitHeight + 40
            color: theme.surface; border.color: theme.line; radius: 12
            ColumnLayout {
                id: enabledContent
                anchors { left: parent.left; right: parent.right; top: parent.top; margins: 20 }
                spacing: 16
                RowLayout {
                    Layout.fillWidth: true; spacing: 16
                    Label { text: "离开锁屏与响指解锁"; color: theme.text; font.pixelSize: 22; font.weight: Font.Medium }
                    FigmaEnableControl {
                        objectName: "lockPageEnabled"
                        checked: page.proximity.enabled; enabled: !page.proximity.busy
                        Accessible.name: "离开锁屏与响指解锁"
                        onClicked: page.proximity.enabled = !page.proximity.enabled
                    }
                    Item { Layout.fillWidth: true }
                    FigmaAction { text: "映射调节"; navigation: true; enabled: false }
                }
                RowLayout {
                    spacing: 12
                    FigmaIcon { symbol: "snap" }
                    Label { text: "响指"; color: theme.text; font.pixelSize: 16; font.weight: Font.Medium }
                    Label { text: "解锁屏幕"; color: theme.muted; font.pixelSize: 14 }
                }
                Label { Layout.fillWidth: true; visible: text.length > 0; text: page.proximity.error; color: theme.warning; font.pixelSize: 14; wrapMode: Text.Wrap }
            }
        }
        Rectangle {
            objectName: "lockPreparationCard"
            width: parent.width; height: preparation.implicitHeight + 40
            color: theme.surface; border.color: theme.line; radius: 12
            ColumnLayout {
                id: preparation
                anchors { left: parent.left; right: parent.right; top: parent.top; margins: 20 }
                spacing: 16
                Label { text: "使用准备"; font.pixelSize: 22; font.weight: Font.Medium; color: theme.text }
                Label { text: "查看准备状态，或重新校准当前 Ring。"; color: theme.muted; font.pixelSize: 14; wrapMode: Text.Wrap; Layout.fillWidth: true }
                RowLayout {
                    Layout.fillWidth: true; spacing: 12
                    Label { Layout.fillWidth: true; text: "1. 桌前校准"; color: theme.text; font.pixelSize: 16 }
                    Label { objectName: "lockCalibrationStatus"; text: page.hasCalibration ? "已完成" : "未完成"; color: page.hasCalibration ? theme.success : theme.warning; font.pixelSize: 14 }
                    FigmaAction { objectName: "lockCalibrateButton"; text: page.hasCalibration ? "重新校准" : "开始校准"; navigation: true; onClicked: calibrationDialog.open() }
                }
                RowLayout {
                    Layout.fillWidth: true; spacing: 12
                    Label { Layout.fillWidth: true; text: "2. 解锁密码"; color: theme.text; font.pixelSize: 16 }
                    Label { objectName: "lockPasswordStatus"; text: page.proximity.passwordConfigured ? "已设置" : page.proximity.passwordStatusChecked ? "未设置" : "待确认"; color: page.proximity.passwordConfigured ? theme.success : theme.warning; font.pixelSize: 14 }
                    FigmaAction { objectName: "lockPasswordButton"; text: page.proximity.passwordConfigured ? "修改密码" : "设置密码"; navigation: true; enabled: !page.proximity.busy; onClicked: page.proximity.configurePassword() }
                }
                RowLayout {
                    Layout.fillWidth: true; spacing: 12
                    Label { Layout.fillWidth: true; text: "3. 权限检查"; color: theme.text; font.pixelSize: 16 }
                    Label { objectName: "lockPermissionStatus"; text: page.proximity.permissionReady ? "已开启" : page.proximity.passwordStatusChecked ? "未开启" : "待确认"; color: page.proximity.permissionReady ? theme.success : theme.warning; font.pixelSize: 14 }
                    FigmaAction { objectName: "lockPermissionButton"; text: "前往设置"; navigation: true; onClicked: page.settingsRequested() }
                }
            }
        }
        Rectangle {
            width: parent.width; height: preferences.implicitHeight + 40
            color: theme.surface; border.color: theme.line; radius: 12
            ColumnLayout {
                id: preferences
                anchors { left: parent.left; right: parent.right; top: parent.top; margins: 20 }
                spacing: 12
                RowLayout {
                    Layout.fillWidth: true
                    Label { Layout.fillWidth: true; text: "偏好调节"; color: theme.text; font.pixelSize: 22; font.weight: Font.Medium }
                    FigmaAction { objectName: "lockAdvancedButton"; text: "高级设置"; navigation: true; onClicked: advanced.open() }
                }
                RowLayout {
                    Layout.fillWidth: true; spacing: 16
                    ColumnLayout {
                        Layout.fillWidth: true; spacing: 4
                        Label { text: "自动适应距离"; color: theme.text; font.pixelSize: 16 }
                        Label { Layout.fillWidth: true; text: "根据日常操作微调距离判断，减少长期使用的偏差。"; color: theme.muted; font.pixelSize: 14; wrapMode: Text.Wrap }
                    }
                    FigmaSwitch {
                        objectName: "lockAdaptiveSwitch"
                        checked: page.proximity.baselineOptions.enabled; enabled: !page.proximity.busy
                        onClicked: { page.proximity.setBaselineOption("enabled", checked); checked = Qt.binding(function() { return page.proximity.baselineOptions.enabled }) }
                    }
                }
                Rectangle { Layout.fillWidth: true; height: 1; color: theme.line }
                RowLayout {
                    Layout.fillWidth: true
                    Label { Layout.fillWidth: true; text: "锁屏提示"; color: theme.text; font.pixelSize: 16 }
                    FigmaAction { objectName: "lockMessageButton"; text: "设置提示"; navigation: true; onClicked: messageDialog.open() }
                }
                Rectangle {
                    Layout.fillWidth: true; implicitHeight: 48; radius: 8; color: theme.subtle
                    Label { anchors.fill: parent; anchors.margins: 12; text: "Ring 已连接且功能开启时，可响指解锁。"; verticalAlignment: Text.AlignVCenter; color: theme.text; font.pixelSize: 16 }
                }
            }
        }
    }
    FigmaDialog {
        id: messageDialog
        objectName: "lockMessageDialog"
        parent: Overlay.overlay; anchors.centerIn: parent; popupType: Popup.Item
        width: Math.min(520, parent.width - 48); padding: 24
        modal: true; title: "锁屏提示"
        background: Rectangle { color: theme.surface; radius: 12; border.color: theme.line }
        contentItem: ColumnLayout {
            spacing: 16
            Label { Layout.fillWidth: true; text: "提示通过系统锁屏留言显示，需手动保存。"; font.pixelSize: 14; color: theme.muted; wrapMode: Text.Wrap }
            Rectangle { Layout.fillWidth: true; implicitHeight: 86; radius: 8; color: theme.subtle; Label { anchors.fill: parent; anchors.margins: 16; text: "建议留言\nRing 已连接且功能开启时，可响指解锁屏幕。"; color: theme.text; font.pixelSize: 16; wrapMode: Text.Wrap } }
            Label { Layout.fillWidth: true; text: "1. 复制提示文字。\n2. 打开系统锁屏设置，开启锁定时显示信息。\n3. 粘贴并保存；如有留言，请保留原文后追加。\n\n这是静态留言，手动锁屏或退出应用后仍会显示。"; color: theme.muted; font.pixelSize: 14; wrapMode: Text.Wrap }
            RowLayout {
                FigmaAction { text: "复制提示文字"; onClicked: page.proximity.copyLockScreenMessage() }
                FigmaAction { text: "打开系统锁屏设置"; primary: true; onClicked: page.proximity.openLockScreenSettings() }
            }
            Label { Layout.fillWidth: true; visible: text.length > 0; text: page.proximity.lockScreenMessageStatus; color: theme.muted; wrapMode: Text.Wrap }
            FigmaAction { Layout.alignment: Qt.AlignRight; text: "关闭"; quiet: true; onClicked: messageDialog.close() }
        }
    }
    FigmaDialog {
        id: advanced
        objectName: "lockAdvancedDialog"
        parent: Overlay.overlay; anchors.centerIn: parent; popupType: Popup.Item
        width: Math.min(720, parent.width - 48); height: Math.min(633, parent.height - 48)
        padding: 24; title: "高级设置"; modal: true
        background: Rectangle { color: theme.surface; radius: 12; border.color: theme.line }
        contentItem: ColumnLayout {
            spacing: 16
            Label { Layout.fillWidth: true; text: "自动锁屏 · 更改自动保存，启用功能后生效。"; color: theme.muted; font.pixelSize: 14 }
            Label { text: "距离判定"; color: theme.text; font.pixelSize: 18; font.weight: Font.Medium }
            Repeater {
                model: [{key:"awaySamples",title:"离开确认次数",description:"连续检测到弱信号后，再确认离开。",min:1,max:60}, {key:"lostDelay",title:"断连确认时长",description:"断连不锁屏，此时长只影响返回亮屏判断。",min:5,max:120}]
                RowLayout {
                    required property var modelData
                    Layout.fillWidth: true; spacing: 20
                    ColumnLayout { Layout.fillWidth: true; spacing: 4; Label { text: modelData.title; color: theme.text; font.pixelSize: 16 } Label { Layout.fillWidth: true; text: modelData.description; color: theme.muted; font.pixelSize: 14; wrapMode: Text.Wrap } }
                    FigmaStepper { unit: modelData.key === "awaySamples" ? " 次" : " 秒"; Layout.preferredWidth: 144; from: modelData.min; to: modelData.max; value: page.proximity.options[modelData.key]; enabled: !page.proximity.enabled; onValueModified: page.proximity.setOption(modelData.key, value) }
                }
            }
            Rectangle { Layout.fillWidth: true; height: 1; color: theme.line }
            Label { text: "自动适应 · 采样来源"; color: theme.text; font.pixelSize: 18; font.weight: Font.Medium }
            Repeater {
                model: [{key:"keyboardMouse",title:"键盘与鼠标"},{key:"gestures",title:"戒指手势"},{key:"voice",title:"语音输入"}]
                RowLayout {
                    required property var modelData
                    Layout.fillWidth: true
                    Label { Layout.fillWidth: true; text: modelData.title; color: theme.muted; font.pixelSize: 16 }
                    FigmaSwitch { checked: page.proximity.baselineOptions[modelData.key]; enabled: !page.proximity.busy && page.proximity.baselineOptions.enabled; onClicked: { page.proximity.setBaselineOption(modelData.key, checked); checked = Qt.binding(function() { return page.proximity.baselineOptions[modelData.key] }) } }
                }
            }
            Item { Layout.fillHeight: true }
            RowLayout {
                Layout.fillWidth: true
                Label { Layout.fillWidth: true; text: page.hasCalibration ? "已保存桌前校准" : "尚未校准 · 同一枚戒指重连后沿用"; color: theme.muted; font.pixelSize: 14 }
                FigmaAction { text: "授权排查"; navigation: true; onClicked: { advanced.close(); page.settingsRequested() } }
            }
            FigmaAction { Layout.alignment: Qt.AlignRight; text: "完成"; primary: true; onClicked: advanced.close() }
        }
    }
    FigmaDialog {
        id: calibrationDialog
        objectName: "lockCalibrationDialog"
        parent: Overlay.overlay; anchors.centerIn: parent; popupType: Popup.Item
        width: Math.min(480, parent.width - 48); padding: 24; title: "桌前校准"; modal: true
        background: Rectangle { color: theme.surface; radius: 12; border.color: theme.line }
        contentItem: ColumnLayout {
            spacing: 20
            Label { Layout.fillWidth: true; text: "戴好戒指，坐在电脑前保持正常使用姿势，采集 10 秒。"; color: theme.muted; font.pixelSize: 16; wrapMode: Text.Wrap }
            ProgressBar { Layout.fillWidth: true; visible: page.proximity.calibrating; from: 0; to: 10; value: page.proximity.calibrationElapsed }
            Label { Layout.fillWidth: true; text: page.proximity.calibrationStatus; color: theme.muted; font.pixelSize: 14; wrapMode: Text.Wrap }
            RowLayout {
                FigmaAction { text: page.proximity.calibrating ? "取消校准" : "关闭"; onClicked: { if (page.proximity.calibrating) page.proximity.cancelCalibration(); calibrationDialog.close() } }
                FigmaAction { text: "开始 10 秒校准"; primary: true; enabled: !page.proximity.busy; onClicked: page.proximity.startCalibration() }
            }
        }
    }
}
