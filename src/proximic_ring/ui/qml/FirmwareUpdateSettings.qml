import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

ColumnLayout {
    id: pane
    required property var updater
    spacing: 20
    FigmaTheme { id: theme }
    FigmaFormGroup {
        flat: true
        title: "戒指固件更新"; symbol: "bluetooth"
        description: "通过蓝牙，将随应用提供的固件更新到当前连接的戒指。"
        FigmaFormRow {
            preferenceStyle: true; title: "当前设备"
            Label { Layout.fillWidth: true; text: pane.updater.targetName; color: theme.text; wrapMode: Text.Wrap }
        }
        FigmaFormRow {
            preferenceStyle: true; title: "当前固件"
            Label { objectName: "currentFirmwareVersion"; Layout.fillWidth: true; text: pane.updater.currentVersion; color: theme.text; wrapMode: Text.Wrap }
        }
        FigmaFormRow {
            preferenceStyle: true; title: "随应用固件"
            Label { objectName: "bundledFirmwareVersion"; Layout.fillWidth: true; text: pane.updater.bundledVersion; color: theme.text; wrapMode: Text.Wrap }
        }
    }
    Label {
        Layout.fillWidth: true
        text: "更新需要戒指电量至少 30%。请将戒指放在电脑附近，更新期间保持电脑唤醒，不要关闭应用或关闭蓝牙。语音与手势会暂时停用，完成后自动重连。"
        color: theme.muted; font.pixelSize: 14; wrapMode: Text.Wrap
    }
    ProgressBar {
        objectName: "firmwareUpdateProgress"
        Layout.fillWidth: true
        visible: pane.updater.busy
        from: 0; to: 100; value: pane.updater.progress
        indeterminate: pane.updater.phase !== "uploading"
        Accessible.name: "固件更新进度"
    }
    UiNotice {
        objectName: "firmwareUpdateStatus"
        Layout.fillWidth: true
        visible: text.length > 0
        text: pane.updater.message
        warning: pane.updater.error
        wrapMode: Text.Wrap
    }
    Label {
        Layout.fillWidth: true
        visible: !pane.updater.busy && text.length > 0
        text: pane.updater.unavailableReason
        color: theme.muted; font.pixelSize: 13; wrapMode: Text.Wrap
    }
    FigmaAction {
        objectName: "startFirmwareUpdateButton"
        text: pane.updater.busy ? "更新中…" : "更新戒指固件"
        primary: true
        enabled: pane.updater.canStart
        onClicked: confirmation.open()
    }
    FigmaDialog {
        id: confirmation
        objectName: "firmwareUpdateConfirmation"
        parent: Overlay.overlay
        anchors.centerIn: parent
        width: Math.min(480, parent.width - 48)
        modal: true
        title: "确认更新固件"
        contentItem: ColumnLayout {
            spacing: 16
            Label {
                Layout.fillWidth: true
                text: "将为 " + pane.updater.targetName + " 安装 " + pane.updater.bundledVersion + "。\n当前版本：" + pane.updater.currentVersion + "。\n\n更新会重启戒指。请保持电脑唤醒，并等待更新验证和重连完成。"
                color: theme.text; wrapMode: Text.Wrap; font.pixelSize: 14
            }
            RowLayout {
                Layout.alignment: Qt.AlignRight
                FigmaAction { text: "取消"; quiet: true; onClicked: confirmation.close() }
                FigmaAction {
                    objectName: "confirmFirmwareUpdateButton"
                    text: "开始更新"; primary: true; enabled: pane.updater.canStart
                    onClicked: { confirmation.close(); pane.updater.start() }
                }
            }
        }
    }
}
