import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

ColumnLayout {
    id: pane
    required property var controller
    required property var settingsDialog
    signal inputMethodSetupRequested()
    signal detailRequested(int page)
    signal advancedSettingsRequested()
    spacing: 36
    UiTheme { id: theme }

    SettingsFormGroup {
        objectName: "settingsAudioGroup"
        title: "麦克风与语音"; symbol: "microphone"
        description: "连接期间可修改；正在进行的语音处理完成后，再应用新的音频来源与启停方式。"
        Label {
            objectName: "audioSettingsStatus"
            Layout.fillWidth: true
            visible: text.length > 0
            text: pane.controller.audioSettingsStatus
            color: pane.controller.audioSettingsError ? "#9A6817" : theme.muted
            font.pixelSize: 13; wrapMode: Text.Wrap
        }
        SettingsFormRow {
            title: "音频来源"
            description: pane.controller.microphoneDevicesError.length > 0
                ? pane.controller.microphoneDevicesError
                : "直接选择 Ring 或已检测到的音频输入设备。"
            SettingsSelect {
                id: audioInputCombo
                objectName: "audioSourceCombo"
                Layout.fillWidth: true
                Accessible.name: "音频来源"
                model: pane.controller.audioInputs
                textRole: "label"; valueRole: "value"
                enabled: !pane.controller.busy
                currentIndex: {
                    var rows = pane.controller.audioInputs
                    for (var i = 0; i < rows.length; ++i)
                        if (rows[i].value === pane.controller.audioInputSelection) return i
                    return -1
                }
                displayText: currentIndex >= 0 ? currentText : pane.controller.audioInputLabel
                onActivated: pane.controller.selectAudioInput(currentValue)
                Connections {
                    target: audioInputCombo.popup
                    function onAboutToShow() { pane.controller.refreshMicrophones() }
                }
            }
            UiAction {
                objectName: "refreshMicrophonesButton"
                Layout.alignment: Qt.AlignRight
                text: pane.controller.microphoneScanBusy ? "检测中…" : "刷新设备"
                quiet: true
                enabled: !pane.controller.microphoneScanBusy
                onClicked: pane.controller.refreshMicrophones()
            }
        }
        PermissionNotice {
            objectName: "microphoneSettingsPermissionNotice"
            Layout.fillWidth: true
            readonly property var warning: pane.controller.inlineInput.permissions.essentialWarnings.filter(function(item) { return item.kind === "microphone" })[0]
            message: pane.controller.audioSettings.audio_source === "microphone" && warning ? warning.text : ""
            onActivated: pane.controller.inlineInput.permissions.openPermissionSettings("microphone")
        }
        SettingsFormRow {
            title: "语音启停方式"
            descriptionObjectName: "speechControlModeHint"
            description: (pane.controller.audioSettings.speech_control_mode === "gesture"
                ? "开启语音识别后，" + pane.controller.confirmGestureHint + " 开始，再做一次结束。"
                : "靠近说话开始，" + pane.controller.confirmGestureHint + " 结束。"
                  + (pane.controller.audioSettings.audio_source === "microphone" ? "使用选定麦克风检测靠近。" : ""))
            SettingsSelect {
                objectName: "speechControlModeCombo"
                Layout.fillWidth: true
                Accessible.name: "语音启停方式"
                model: ["靠近说话", "手势启停"]
                enabled: !pane.controller.busy
                currentIndex: pane.controller.audioSettings.speech_control_mode === "gesture" ? 1 : 0
                onActivated: pane.controller.speechControlMode = currentIndex === 1 ? "gesture" : "proximity"
            }
        }
        SettingsFormRow {
            objectName: "inputEnhancementRow"
            title: "输入增强"
            description: "声音较小时可提高音量，立即生效；不影响靠近检测。"
            Slider {
                objectName: "asrGainSlider"
                Layout.fillWidth: true
                Accessible.name: "输入增强"
                from: 0; to: 12; stepSize: 1
                value: pane.controller.asrGainDb
                onMoved: pane.controller.asrGainDb = value
            }
            Label {
                Layout.alignment: Qt.AlignRight
                text: "+" + pane.controller.asrGainDb.toFixed(0) + " dB"
                color: theme.muted; font.pixelSize: 12
            }
        }
        SettingsFormRow {
            objectName: "voiceSensitivityRow"
            visible: pane.controller.speechControlMode === "proximity"
            title: "声音触发灵敏度"
            description: "数值越高越容易触发；连接期间调整会立即生效。"
            Slider {
                id: sensitivity
                objectName: "stage1SensitivitySlider"
                Accessible.name: "声音触发灵敏度"
                Layout.fillWidth: true
                enabled: pane.controller.speechControlMode === "proximity"
                from: 1; to: 10; stepSize: 1
                value: pane.settingsDialog.stage1Sensitivity(pane.controller.stage1Threshold)
                onMoved: pane.controller.stage1Threshold = pane.settingsDialog.thresholdForSensitivity(value)
            }
            Label {
                Layout.alignment: Qt.AlignRight
                text: Math.round(sensitivity.value) + " / 10"
                color: theme.muted; font.pixelSize: 12
            }
        }
    }

    SettingsFormGroup {
        objectName: "settingsInputGroup"
        title: "输入与控制"; symbol: "pointer"
        SettingsFormRow {
            visible: pane.controller.inlineInput.enabled
            title: "多次撤销"
            description: "逐次撤回当前输入框内的听写和编辑；关闭时只保留最近一次操作。"
            SettingsSwitch {
                objectName: "multiUndoSwitch"
                Layout.alignment: Qt.AlignRight
                Accessible.name: "多次撤销"
                checked: pane.controller.multiUndoEnabled
                onClicked: pane.controller.multiUndoEnabled = checked
            }
        }
        SettingsFormRow {
            title: "类型转换按键"
            description: "仅在当前语音结果可以转换时生效，连接期间也可修改。"
            SettingsSelect {
                objectName: "modeCorrectionShortcutCombo"
                Layout.fillWidth: true
                Accessible.name: "类型转换按键"
                model: pane.controller.modeCorrectionShortcutOptions
                currentIndex: Math.max(0, pane.controller.modeCorrectionShortcutOptions.indexOf(pane.controller.modeCorrectionShortcut))
                onActivated: pane.controller.modeCorrectionShortcut = pane.controller.modeCorrectionShortcutOptions[currentIndex]
            }
        }
        SettingsFormRow {
            visible: !pane.controller.inlineInput.enabled
            title: "听写与指令识别"
            description: pane.controller.inputRoutingMode === "auto"
                ? "语音结束后自动判断听写或编辑指令。"
                : "沿用当前选择的听写或编辑模式。"
            SettingsSelect {
                objectName: "inputRoutingModeCombo"
                Layout.fillWidth: true
                Accessible.name: "听写与指令识别"
                model: ["自动判断", "手动选择"]
                currentIndex: pane.controller.inputRoutingMode === "auto" ? 0 : 1
                onActivated: pane.controller.inputRoutingMode = currentIndex === 0 ? "auto" : "manual"
            }
        }
        SettingsFormRow {
            visible: !pane.controller.inlineInput.enabled
            title: "整理听写文本"
            description: "关闭后直接使用识别结果；编辑指令仍会使用文本模型。"
            SettingsSwitch {
                objectName: "dictationLlmSwitch"
                Layout.alignment: Qt.AlignRight
                Accessible.name: "使用大模型整理听写文本"
                checked: pane.controller.llmEnabled
                onClicked: pane.controller.llmEnabled = checked
            }
        }
        SettingsFormRow {
            visible: Qt.platform.os === "windows" || Qt.platform.os === "osx"
            title: "输入到当前光标"
            description: "识别完成后，将结果输入到当前文本框。"
            SettingsSwitch {
                objectName: "desktopOutputSwitch"
                Layout.alignment: Qt.AlignRight
                Accessible.name: "识别完成后输入到当前光标"
                checked: pane.controller.desktopOutputEnabled
                onClicked: pane.controller.desktopOutputEnabled = checked
            }
        }
        SettingsFormRow {
            visible: Qt.platform.os === "windows" && pane.controller.speechControlMode !== "gesture"
            title: "右 Alt 按住说话"
            description: "识别开启时，按键优先于自动靠近检测。"
            SettingsSwitch {
                objectName: "pushToTalkSwitch"
                Layout.alignment: Qt.AlignRight
                Accessible.name: "启用右 Alt 按住说话"
                enabled: !pane.settingsDialog.deviceSettingsLocked && pane.controller.speechControlMode !== "gesture"
                checked: pane.controller.pushToTalkEnabled
                onClicked: pane.controller.pushToTalkEnabled = checked
            }
        }
    }

    SettingsFormGroup {
        objectName: "settingsPermissionsGroup"
        title: "系统与权限"; symbol: "tool"
        visible: Qt.platform.os === "osx"
        description: "管理语音输入与手势控制所需的权限。"
        SettingsFormRow {
            title: "系统授权"
            description: "直接显示 macOS 授权提示，已开启的自动跳过。"
            UiAction {
                objectName: "permissionSetupButton"
                Layout.alignment: Qt.AlignRight
                text: pane.controller.permissionSetup.active ? "继续授权" : "请求权限"
                enabled: !pane.controller.permissionSetup.busy
                onClicked: pane.controller.permissionSetup.open()
            }
        }
        SettingsFormRow {
            title: "辅助功能"
            description: pane.controller.inlineInput.permissions.accessibilityWarningText
                || (pane.controller.inlineInput.permissions.accessibilityGranted ? "已开启" : "用于语音编辑与手势控制。")
            descriptionColor: pane.controller.inlineInput.permissions.accessibilityWarningText ? "#9A6817" : theme.muted
            UiAction {
                objectName: "settingsAccessibilityButton"
                Layout.alignment: Qt.AlignRight
                text: "去系统设置"
                onClicked: pane.controller.inlineInput.permissions.openSettings()
            }
        }
        SettingsFormRow {
            visible: pane.controller.inlineInput.enabled
            title: "语音输入法"
            description: pane.controller.inlineInput.connectionStatus
            UiAction {
                objectName: "settingsInputMethodButton"
                Layout.alignment: Qt.AlignRight
                text: "安装与管理"
                onClicked: pane.inputMethodSetupRequested()
            }
        }
        SettingsFormRow {
            title: "窗口实时预览"
            description: pane.controller.inlineInput.permissions.screenRecordingWarning ? "未开启屏幕录制权限，无法显示窗口预览。"
                : pane.controller.inlineInput.permissions.screenRecordingGranted ? "已开启" : "用于显示窗口实时画面。"
            descriptionColor: pane.controller.inlineInput.permissions.screenRecordingWarning ? "#9A6817" : theme.muted
            UiAction {
                objectName: "settingsCategory6"
                Layout.alignment: Qt.AlignRight
                text: "管理"
                onClicked: pane.detailRequested(6)
            }
        }
    }

    SettingsFormGroup {
        objectName: "settingsGeneralGroup"
        title: "通用"
        SettingsFormRow {
            visible: Qt.platform.os === "osx"
            title: "菜单栏状态"
            description: "显示连接状态和电量，关闭后只保留应用图标。"
            SettingsSwitch {
                objectName: "menuBarStatusInfoSwitch"
                Layout.alignment: Qt.AlignRight
                Accessible.name: "显示菜单栏连接状态和电量"
                checked: pane.controller.menuBarStatusInfo
                onClicked: pane.controller.menuBarStatusInfo = checked
            }
        }
        SettingsFormRow {
            visible: Qt.platform.os === "osx"
            title: "离开锁屏"
            description: "戒指远离时锁定电脑，返回后弹指解锁。"
            UiAction {
                objectName: "settingsCategory9"
                Layout.alignment: Qt.AlignRight
                text: pane.controller.proximity.enabled ? "已开启 · 管理" : "设置"
                onClicked: pane.detailRequested(9)
            }
        }
        SettingsFormRow {
            title: "高级设置"
            description: "识别服务、文本模型与性能"
            UiAction {
                objectName: "advancedSettingsButton"
                Layout.alignment: Qt.AlignRight
                text: "高级设置"
                onClicked: pane.advancedSettingsRequested()
            }
        }
    }
}
