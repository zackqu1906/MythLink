import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

ColumnLayout {
    id: pane
    required property var controller
    required property var host
    component ActionButton: Button {
        implicitHeight: 40
        leftPadding: 16; rightPadding: 16
        background: Rectangle {
            radius: 16
            color: parent.down ? "#46546C" : parent.hovered ? "#3D485C" : "#343D4D"
            opacity: parent.enabled ? 1 : 0.45
        }
        contentItem: Label {
            text: parent.text
            color: parent.enabled ? "#F5F7FB" : "#8791A1"
            horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter
        }
    }
    spacing: 14
    onVisibleChanged: if (visible) controller.refresh()
    Label {
        Layout.fillWidth: true
        text: "戴着戒指离开后锁屏，回来时自动恢复手势，屏幕亮起后可弹指（snap）解锁。"
        color: "#F5F7FB"; font.pixelSize: 15; wrapMode: Text.Wrap
    }
    Switch {
        id: enableSwitch
        palette.windowText: "#F5F7FB"
        contentItem: Label {
            text: enableSwitch.text; color: "#F5F7FB"
            leftPadding: enableSwitch.indicator.width + enableSwitch.spacing
            verticalAlignment: Text.AlignVCenter
        }
        objectName: "proximityEnabled"
        text: "离开锁屏，弹指解锁"
        checked: pane.controller.enabled
        enabled: !pane.controller.busy
        onClicked: pane.controller.enabled = checked
    }
    Rectangle {
        Layout.fillWidth: true
        implicitHeight: statusColumn.implicitHeight + 24
        radius: 10; color: pane.controller.error ? "#352027" : "#16262A"
        ColumnLayout {
            id: statusColumn
            anchors.fill: parent; anchors.margins: 12; spacing: 5
            Label {
                Layout.fillWidth: true
                text: pane.controller.status
                color: "#F5F7FB"; wrapMode: Text.Wrap
            }
            Label {
                Layout.fillWidth: true; visible: !!pane.controller.error
                text: pane.controller.error
                color: "#FF929A"; wrapMode: Text.Wrap
            }
            Label {
                visible: pane.controller.enabled
                text: "当前信号：" + pane.controller.rssi + " dBm"
                color: "#A4AEC0"
            }
        }
    }
    Label { text: "离开与返回确认"; color: "#F5F7FB"; font.bold: true }
    Label {
        Layout.fillWidth: true; wrapMode: Text.Wrap; color: "#A4AEC0"; font.pixelSize: 12
        text: "远离默认需连续 2 次平滑读数低于远离阈值，中间不达标会重新计数。先确认离开后，1 次有效原始读数达到近处阈值就确认回来并恢复手势。本功能锁屏时，手势就绪后再亮屏，亮起即可弹指解锁。一直在电脑前因无操作而锁屏，不会自动亮屏。每次回来只亮一次，亮屏不会解锁。"
    }
    Label {
        Layout.fillWidth: true
        text: pane.controller.enabled ? "请先关闭上方功能再调整，重新开启后生效。" : "修改会自动保存，开启功能后生效。"
        color: "#A4AEC0"; wrapMode: Text.Wrap; font.pixelSize: 12
    }
    ColumnLayout {
        Layout.fillWidth: true
        enabled: !pane.controller.enabled
        Repeater {
            model: [
                {key: "awaySamples", title: "远离确认（连续读数次数）", min: 1, max: 60},
                {key: "lostDelay", title: "弱信号断连后锁屏等待（秒）", min: 5, max: 120}
            ]
            delegate: RowLayout {
                required property var modelData
                Layout.fillWidth: true
                Label { Layout.fillWidth: true; text: modelData.title; color: "#A4AEC0"; wrapMode: Text.Wrap }
                SpinBox {
                    objectName: "proximity_" + modelData.key
                    from: modelData.min; to: modelData.max
                    editable: true
                    value: pane.controller.options[modelData.key]
                    onValueModified: pane.controller.setOption(modelData.key, value)
                }
            }
        }
    }
    Label { text: "当前戒指"; color: "#F5F7FB"; font.bold: true }
    Label {
        Layout.fillWidth: true
        text: pane.controller.deviceName + "\n自动使用主界面连接的戒指；断连后继续寻找这枚戒指。"
        color: "#A4AEC0"; wrapMode: Text.Wrap
    }
    Label { text: "1. 校准当前戒指"; color: "#F5F7FB"; font.bold: true }
    Label {
        Layout.fillWidth: true; wrapMode: Text.Wrap; color: "#A4AEC0"
        text: "戴好戒指，坐在电脑前并保持正常使用姿势。点击校准后采集 10 秒信号，取中位数作为基线。同一枚戒指重连或应用重启后沿用此结果，只有再次校准才会更新。"
    }
    Label {
        objectName: "proximitySavedCalibration"
        Layout.fillWidth: true; wrapMode: Text.Wrap; color: "#F5F7FB"
        text: pane.controller.savedCalibration.baseline === undefined
              ? "当前戒指尚未校准，完成后才能开启离开锁屏。"
              : "已保存基线：" + Number(pane.controller.savedCalibration.baseline).toFixed(1) + " dBm"
                + "\n远离锁屏：< " + Number(pane.controller.savedCalibration.baseline - 8).toFixed(1) + " dBm"
                + " · 校准样本：" + pane.controller.savedCalibration.samples.length + " 次"
    }
    Label {
        Layout.fillWidth: true; wrapMode: Text.Wrap; color: "#A4AEC0"
        visible: pane.controller.enabled
        text: pane.controller.calibrating ? "校准期间暂停距离监测，结束后自动恢复。"
                                         : "无需关闭上方功能即可校准；采集期间暂停距离监测，结束后自动恢复。"
    }
    Label {
        objectName: "proximityCalibrationStatus"
        Layout.fillWidth: true; wrapMode: Text.Wrap; color: "#A4AEC0"
        visible: !!pane.controller.calibrationStatus
        text: pane.controller.calibrationStatus
    }
    ProgressBar {
        objectName: "proximityCalibrationProgress"
        Layout.fillWidth: true
        visible: pane.controller.calibrating
        from: 0; to: 10; value: pane.controller.calibrationElapsed
    }
    Label {
        Layout.fillWidth: true; color: "#A4AEC0"; wrapMode: Text.Wrap
        visible: pane.controller.calibrating
        text: Number(pane.controller.calibrationElapsed).toFixed(1) + " / 10 秒 · "
              + pane.controller.calibrationSamples + " 个有效样本"
    }
    Flow {
        Layout.fillWidth: true; spacing: 10
        ActionButton {
            objectName: "proximityCalibrate"
            text: pane.controller.savedCalibration.baseline === undefined ? "开始 10 秒校准" : "重新校准（10 秒）"
            enabled: !pane.controller.busy
            onClicked: pane.controller.startCalibration()
        }
        ActionButton {
            text: "取消校准"; visible: pane.controller.calibrating
            onClicked: pane.controller.cancelCalibration()
        }
    }
    Label { text: "2. 设置本机解锁密码"; color: "#F5F7FB"; font.bold: true }
    Label {
        Layout.fillWidth: true
        text: !pane.controller.passwordStatusChecked ? "正在检查已保存的密码授权…"
              : pane.controller.passwordConfigured ? (pane.controller.passwordAccessReady ? "密码和访问授权已记住，无需每次重新设置。更改 Mac 登录密码后，请在这里更新。" : "密码已保存，但当前组件尚未获准读取。请点击允许访问已存密码，在系统弹窗中选择始终允许；普通重启会沿用授权。") : "将弹出本机安全密码输入框。请输入这台 Mac 当前用户的登录密码。"
        color: "#A4AEC0"; wrapMode: Text.Wrap
    }
    Flow {
        Layout.fillWidth: true; spacing: 10
        ActionButton {
            text: pane.controller.passwordConfigured ? "更新解锁密码" : "设置解锁密码"
            enabled: !pane.controller.busy
            onClicked: pane.controller.configurePassword()
        }
        ActionButton {
            objectName: "proximityAuthorizePassword"
            text: "允许访问已存密码"
            visible: pane.controller.passwordConfigured && !pane.controller.passwordAccessReady
            enabled: !pane.controller.busy
            onClicked: pane.controller.authorizePassword()
        }
        ActionButton {
            text: "删除已存密码"; visible: pane.controller.passwordConfigured
            enabled: !pane.controller.busy
            onClicked: pane.controller.forgetPassword()
        }
    }
    Label { text: "3. 允许系统权限"; color: "#F5F7FB"; font.bold: true }
    Label {
        Layout.fillWidth: true
        text: (pane.controller.permissionReady ? "辅助功能权限已就绪。" : "请允许“ProxiMic 距离锁屏”或系统列出的启动应用使用辅助功能。")
              + "开启后还会请求蓝牙权限。若钥匙串询问访问密码，请选“始终允许”。更新临时签名版本后可能需要重新授权。"
        color: "#A4AEC0"; wrapMode: Text.Wrap
    }
    Flow {
        Layout.fillWidth: true; spacing: 10
        ActionButton { text: "打开权限设置"; enabled: !pane.controller.busy; onClicked: pane.controller.requestPermissions() }
        ActionButton { text: "查看组件位置"; onClicked: pane.controller.revealComponent() }
        ActionButton { text: "检查状态"; enabled: !pane.controller.busy; onClicked: pane.controller.refresh() }
    }
    Label { text: "4. 系统锁屏提示"; color: "#F5F7FB"; font.bold: true }
    Label {
        Layout.fillWidth: true
        text: pane.controller.lockScreenMessage
        color: "#F5F7FB"; wrapMode: Text.Wrap
    }
    Label {
        Layout.fillWidth: true
        text: "使用 macOS 自带的锁屏信息。复制提示后，打开系统设置，开启“锁定时显示信息”并粘贴保存。若已有留言，请保留原文并追加。\n这是静态文字，手动锁屏或 Ring 退出后也会显示；可随时在系统设置中修改或关闭。"
        color: "#A4AEC0"; wrapMode: Text.Wrap; font.pixelSize: 12
    }
    Flow {
        Layout.fillWidth: true; spacing: 10
        ActionButton { text: "复制提示文字"; onClicked: pane.controller.copyLockScreenMessage() }
        ActionButton { text: "打开系统锁屏设置"; onClicked: pane.controller.openLockScreenSettings() }
    }
    Label {
        Layout.fillWidth: true
        visible: !!pane.controller.lockScreenMessageStatus
        text: pane.controller.lockScreenMessageStatus
        color: "#A4AEC0"; wrapMode: Text.Wrap; font.pixelSize: 12
    }
    Label {
        Layout.fillWidth: true
        text: "当前戒指连接确认后开始监测。本功能锁屏后，弹指（snap）尝试解锁，不使用信号阈值或等待时间自动解锁。远离断连后会在锁屏期间自动恢复同一枚戒指的手势连接，连接恢复不会解锁；手动锁屏仍使用系统方式解锁。开启期间保持后台监测，屏幕仍可熄灭；合盖或手动睡眠后需先唤醒，重启或注销后仍需手动登录。主程序完全退出后，此功能停止。"
        color: "#A4AEC0"; wrapMode: Text.Wrap; font.pixelSize: 12
    }
    Label {
        Layout.fillWidth: true
        text: "蓝牙信号会受遮挡影响，不能精确测距；持有已连接戒指并做出弹指（snap）手势可触发解锁，请妥善保管。"
        color: "#D7B17A"; wrapMode: Text.Wrap; font.pixelSize: 12
    }

}
