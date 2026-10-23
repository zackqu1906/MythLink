import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

ColumnLayout {
    id: pane
    required property var controller
    required property var host
    property bool showAdvanced: false
    readonly property bool hasCalibration: controller.savedCalibration.baseline !== undefined
    spacing: 18
    FigmaTheme { id: theme }
    onVisibleChanged: {
        if (visible) controller.refresh()
        else showAdvanced = false
    }

    Rectangle {
        Layout.fillWidth: true
        implicitHeight: statusContent.implicitHeight + 40
        radius: 14; color: "#EDF1FF"; border.color: "#DCE3FC"
        ColumnLayout {
            id: statusContent
            anchors { left: parent.left; right: parent.right; top: parent.top; margins: 20 }
            spacing: 14
            FigmaFormRow {
                title: "离开锁屏，弹指解锁"
                description: "戴着戒指离开后自动锁屏。快捷键、菜单或系统空闲锁屏后，也可直接弹指解锁，无需先离开再返回。"
                controlWidth: 48
                FigmaSwitch {
                    objectName: "proximityEnabled"
                    Layout.alignment: Qt.AlignRight
                    Accessible.name: "离开锁屏，弹指解锁"
                    checked: pane.controller.enabled
                    enabled: !pane.controller.busy
                    onClicked: {
                        pane.controller.enabled = checked
                        // An unmet prerequisite can reject enabling; show the actual state.
                        checked = Qt.binding(function() { return pane.controller.enabled })
                    }
                }
            }
            Rectangle { Layout.fillWidth: true; implicitHeight: 1; color: theme.line }
            RowLayout {
                Layout.fillWidth: true; spacing: 8
                UiIcon { symbol: "bluetooth"; Layout.preferredWidth: 18; Layout.preferredHeight: 18 }
                Label {
                    objectName: "proximityDeviceName"
                    Layout.fillWidth: true
                    text: pane.controller.deviceName
                    color: theme.text; font.pixelSize: 13; wrapMode: Text.Wrap
                }
                Label {
                    objectName: "proximityStatus"
                    Layout.maximumWidth: statusContent.width * 0.55
                    text: pane.controller.status
                    color: pane.controller.error ? "#BB4255" : pane.controller.enabled ? theme.success : theme.muted
                    font.pixelSize: 12; wrapMode: Text.Wrap
                }
            }
            Label {
                objectName: "proximityError"
                Layout.fillWidth: true
                visible: text.length > 0
                text: pane.controller.error
                color: "#BB4255"; font.pixelSize: 13; wrapMode: Text.Wrap
            }
        }
    }

    FigmaFormGroup {
        title: "开始使用"
        description: "使用首页连接的戒指，完成以下设置后开启上方功能。"
        FigmaFormRow {
            title: "1. 校准戒指"
            descriptionObjectName: "proximitySavedCalibration"
            description: "戴好戒指，坐在电脑前保持正常使用姿势，采集 10 秒。"
                + (pane.hasCalibration ? "\n已保存校准，重连和重启后仍可使用。" : "\n当前戒指尚未校准。")
            FigmaAction {
                objectName: "proximityCalibrate"
                Layout.alignment: Qt.AlignRight
                text: pane.controller.calibrating ? "正在校准…" : pane.hasCalibration ? "重新校准（10 秒）" : "开始 10 秒校准"
                primary: !pane.hasCalibration
                enabled: !pane.controller.busy
                onClicked: pane.controller.startCalibration()
            }
            FigmaAction {
                objectName: "proximityCancelCalibration"
                Layout.alignment: Qt.AlignRight
                text: "取消校准"
                visible: pane.controller.calibrating
                onClicked: pane.controller.cancelCalibration()
            }
        }
        ColumnLayout {
            Layout.fillWidth: true; spacing: 8
            visible: pane.controller.calibrating || pane.controller.calibrationStatus.length > 0
            ProgressBar {
                objectName: "proximityCalibrationProgress"
                Layout.fillWidth: true
                visible: pane.controller.calibrating
                from: 0; to: 10; value: pane.controller.calibrationElapsed
            }
            Label {
                objectName: "proximityCalibrationStatus"
                Layout.fillWidth: true
                text: pane.controller.calibrating
                    ? (pane.controller.calibrationElapsed === 0 ? "正在连接戒指…" : Number(pane.controller.calibrationElapsed).toFixed(1) + " / 10 秒，请保持正常坐姿。")
                      + (pane.controller.enabled ? "\n校准期间暂停距离监测，结束后自动恢复。" : "")
                    : pane.controller.calibrationStatus
                color: theme.muted; font.pixelSize: 13; wrapMode: Text.Wrap
            }
        }
        FigmaFormRow {
            title: "2. 解锁密码"
            descriptionObjectName: "proximityPasswordStatus"
            description: !pane.controller.passwordStatusChecked ? "正在检查已保存的密码授权…"
                : !pane.controller.passwordConfigured ? "通过系统安全输入框，设置这台 Mac 的登录密码。"
                : !pane.controller.passwordAccessReady ? "密码已保存，请允许当前组件访问，并在系统弹窗中选择“始终允许”。"
                : "密码与访问授权已就绪；更改 Mac 登录密码后，请在这里更新。"
            FigmaAction {
                objectName: "proximityConfigurePassword"
                Layout.alignment: Qt.AlignRight
                text: pane.controller.passwordConfigured ? "更新解锁密码" : "设置解锁密码"
                enabled: !pane.controller.busy
                onClicked: pane.controller.configurePassword()
            }
            FigmaAction {
                objectName: "proximityAuthorizePassword"
                Layout.alignment: Qt.AlignRight
                text: "允许访问已存密码"
                visible: pane.controller.passwordConfigured && !pane.controller.passwordAccessReady
                enabled: !pane.controller.busy
                onClicked: pane.controller.authorizePassword()
            }
            FigmaAction {
                objectName: "proximityForgetPassword"
                Layout.alignment: Qt.AlignRight
                text: "删除已存密码"; quiet: true
                visible: pane.controller.passwordConfigured
                enabled: !pane.controller.busy
                onClicked: pane.controller.forgetPassword()
            }
        }
        Label {
            Layout.fillWidth: true
            visible: pane.controller.enabled
            text: "修改密码或重新授权会关闭离开锁屏，完成后请手动开启。"
            color: theme.muted; font.pixelSize: 12; wrapMode: Text.Wrap
        }
    }

    FigmaFormGroup {
        title: "自动更新基线"
        description: "选择哪些操作可以用于采集桌前信号，减轻长期漂移的影响。"
        FigmaFormRow {
            title: "根据操作更新基线"
            description: "开启后，在所选操作后的 15 秒内采样。最近 120 秒内至少 5 个有效读数、跨度至少 2 秒即可更新。键鼠操作及已启用的手势、语音证据可保护 10 秒不因远离锁屏，不必等基线更新完成。"
            controlWidth: 48
            FigmaSwitch {
                objectName: "proximityBaseline_enabled"
                Layout.alignment: Qt.AlignRight
                Accessible.name: "根据操作更新基线"
                checked: pane.controller.baselineOptions.enabled
                enabled: !pane.controller.busy
                onClicked: {
                    pane.controller.setBaselineOption("enabled", checked)
                    checked = Qt.binding(function() { return pane.controller.baselineOptions.enabled })
                }
            }
        }
        Repeater {
            model: [
                { key: "keyboardMouse", title: "键盘和鼠标操作", description: "将系统检测到的键鼠操作作为采样依据。" },
                { key: "gestures", title: "戒指手势操作", description: "将当前戒指的有效手势作为采样依据；远处操作也会参与更新。" },
                { key: "voice", title: "语音输入", description: "将本应用新识别到的语音文字作为采样依据；远处输入也会参与更新。" }
            ]
            delegate: FigmaFormRow {
                required property var modelData
                title: modelData.title
                description: modelData.description
                controlWidth: 48
                FigmaSwitch {
                    objectName: "proximityBaseline_" + modelData.key
                    Layout.alignment: Qt.AlignRight
                    Accessible.name: modelData.title + "用于更新基线"
                    checked: pane.controller.baselineOptions[modelData.key]
                    enabled: !pane.controller.busy
                    onClicked: {
                        pane.controller.setBaselineOption(modelData.key, checked)
                        checked = Qt.binding(function() { return pane.controller.baselineOptions[modelData.key] })
                    }
                }
            }
        }
        Label {
            objectName: "proximityBaselineProgress"
            Layout.fillWidth: true
            text: !pane.controller.baselineOptions.enabled ? "已关闭自动更新，保留当前基线。"
                : !pane.controller.baselineOptions.keyboardMouse && !pane.controller.baselineOptions.gestures && !pane.controller.baselineOptions.voice
                    ? "请至少开启一种操作来源。"
                : !pane.controller.enabled ? "开启离开锁屏后开始自动采样；请先完成一次手动校准。"
                : "本轮有效读数：" + (pane.controller.learnedThresholds.activity_calibration_samples || 0)
                    + " / 5" + (pane.controller.learnedThresholds.activity_lock_protected ? " · 近期操作保护中" : "")
                    + "。更新时同时调整靠近阈值（基线 − 4 dB）和远离阈值（基线 − 8 dB），并保存到当前戒指。"
            color: theme.muted; font.pixelSize: 12; wrapMode: Text.Wrap
        }
    }

    FigmaFormGroup {
        title: "锁屏提示"
        description: "可选：在系统锁屏界面显示弹指解锁的使用提示。"
        FigmaFormRow {
            title: "提示文字"
            description: pane.controller.lockScreenMessage
            FigmaAction {
                objectName: "proximityCopyLockMessage"
                Layout.alignment: Qt.AlignRight
                text: "复制提示文字"
                onClicked: pane.controller.copyLockScreenMessage()
            }
            FigmaAction {
                objectName: "proximityOpenLockSettings"
                Layout.alignment: Qt.AlignRight
                text: "打开系统锁屏设置"
                onClicked: pane.controller.openLockScreenSettings()
            }
        }
        Label {
            Layout.fillWidth: true
            text: "在系统设置中开启“锁定时显示信息”，粘贴并保存；已有留言请保留原文并追加。这是静态提示，手动锁屏或退出应用后也会显示，可随时在系统设置中修改或关闭。"
            color: theme.muted; font.pixelSize: 12; wrapMode: Text.Wrap
        }
        Label {
            objectName: "proximityLockMessageStatus"
            Layout.fillWidth: true
            visible: text.length > 0
            text: pane.controller.lockScreenMessageStatus
            color: theme.muted; font.pixelSize: 12; wrapMode: Text.Wrap
        }
    }

    FigmaAction {
        objectName: "proximityAdvancedButton"
        Layout.alignment: Qt.AlignRight
        text: pane.showAdvanced ? "收起高级选项  ⌃" : "高级选项  ⌄"
        onClicked: pane.showAdvanced = !pane.showAdvanced
    }
    FigmaFormGroup {
        objectName: "proximityAdvancedSettings"
        title: "距离检测"
        visible: pane.showAdvanced
        description: pane.controller.enabled ? "关闭上方功能后才能调整参数，重新开启后生效。" : "修改自动保存，开启功能后生效。"
        FigmaFormRow {
            title: "远离确认次数"
            description: "连续多次检测到弱信号后确认离开，中间不达标会重新计数。"
            SpinBox {
                objectName: "proximity_awaySamples"
                Layout.fillWidth: true
                Accessible.name: "远离确认次数"
                from: 1; to: 60; editable: true
                enabled: !pane.controller.enabled
                value: pane.controller.options.awaySamples
                onValueModified: pane.controller.setOption("awaySamples", value)
            }
        }
        FigmaFormRow {
            title: "断连后的离开确认"
            description: "弱信号断连持续达到此时间后，回来时可自动亮屏；断连期间不会锁屏。"
            SpinBox {
                objectName: "proximity_lostDelay"
                Layout.fillWidth: true
                Accessible.name: "弱信号断连后返回检测等待秒数"
                from: 5; to: 120; editable: true
                enabled: !pane.controller.enabled
                value: pane.controller.options.lostDelay
                onValueModified: pane.controller.setOption("lostDelay", value)
            }
        }
        FigmaFormRow {
            visible: pane.controller.enabled
            title: "当前信号"
            description: "信号强度会受遮挡影响，不能精确测距。"
            Label {
                objectName: "proximitySignal"
                Layout.alignment: Qt.AlignRight
                text: pane.controller.rssi === "—" ? "—" : pane.controller.rssi + " dBm"
                color: theme.text; font.pixelSize: 14
            }
        }
        FigmaFormRow {
            title: "已保存的校准"
            description: "手动校准或自动采样完成后更新，同一枚戒指重连时沿用。"
            Label {
                objectName: "proximityCalibrationDetails"
                Layout.fillWidth: true
                horizontalAlignment: Text.AlignRight
                text: !pane.hasCalibration ? "尚未校准"
                    : "基线 " + Number(pane.controller.savedCalibration.baseline).toFixed(1) + " dBm"
                      + "\n靠近阈值 ≥ " + Number(pane.controller.savedCalibration.baseline - 4).toFixed(1) + " dBm"
                      + "\n远离阈值 < " + Number(pane.controller.savedCalibration.baseline - 8).toFixed(1) + " dBm"
                      + "\n" + pane.controller.savedCalibration.samples.length + " 个样本"
                color: theme.muted; font.pixelSize: 12; wrapMode: Text.Wrap
            }
        }
        FigmaFormRow {
            title: "授权排查"
            description: "如授权未生效，核对系统中是否允许“Mythlink 距离锁屏”或对应启动应用；更新后可能需要重新授权。"
            FigmaAction {
                objectName: "proximityRevealComponent"
                Layout.alignment: Qt.AlignRight
                text: "查看组件位置"
                onClicked: pane.controller.revealComponent()
            }
        }
        Label {
            Layout.fillWidth: true
            text: "开启功能后，快捷键、菜单、系统空闲和远离锁屏均支持弹指解锁。同一枚戒指的手势连接就绪后即可使用，无需先离开再返回。确认离开后，一次有效的近处信号可确认返回，等待手势就绪后单次亮屏；亮屏不会直接解锁。\n\n开启期间保持后台监测，屏幕可以熄灭。合盖或手动睡眠后需先唤醒；重启、注销或切换用户后仍需先使用系统方式登录。完全退出应用后，锁屏与解锁功能停止。"
            color: theme.muted; font.pixelSize: 12; wrapMode: Text.Wrap
        }
    }
    Label {
        Layout.fillWidth: true
        text: "功能开启后也支持手动锁屏的弹指解锁。持有已连接戒指的人可触发解锁，请妥善保管戒指。"
        color: theme.muted; font.pixelSize: 12; wrapMode: Text.Wrap
    }
}
