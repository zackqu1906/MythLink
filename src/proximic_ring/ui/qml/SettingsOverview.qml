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
        description: pane.settingsDialog.deviceSettingsLocked
            ? "设备已连接，灰色选项需在首页断开戒指后修改。"
            : "选择输入设备与语音启停方式，设备选项在下次连接时生效。"
        SettingsFormRow {
            title: "音频来源"
            description: "戒指连接统一在首页管理。"
            SettingsSelect {
                objectName: "audioSourceCombo"
                Layout.fillWidth: true
                Accessible.name: "音频来源"
                model: ["Ring 麦克风", "电脑音频"]
                enabled: !pane.settingsDialog.deviceSettingsLocked
                currentIndex: pane.controller.audioSource === "microphone" ? 1 : 0
                onActivated: {
                    pane.controller.audioSource = currentIndex === 1 ? "microphone" : "ring"
                    if (currentIndex === 1) pane.controller.refreshMicrophones()
                }
            }
        }
        SettingsFormRow {
            objectName: "microphoneDeviceRow"
            visible: pane.controller.audioSource === "microphone"
            title: "麦克风"
            description: pane.controller.microphoneDevicesError.length > 0
                ? pane.controller.microphoneDevicesError
                : "选择电脑上的音频输入设备；找不到选定设备时不会切换到其他麦克风。"
            SettingsSelect {
                objectName: "microphoneDeviceCombo"
                Layout.fillWidth: true
                Accessible.name: "选择麦克风"
                enabled: !pane.settingsDialog.deviceSettingsLocked && !pane.controller.microphoneScanBusy
                model: pane.controller.microphoneDevices
                textRole: "label"; valueRole: "value"
                currentIndex: {
                    var rows = pane.controller.microphoneDevices
                    for (var i = 0; i < rows.length; ++i)
                        if (rows[i].value === pane.controller.microphoneDevice) return i
                    return 0
                }
                onActivated: pane.controller.microphoneDevice = currentValue
            }
            UiAction {
                objectName: "refreshMicrophonesButton"
                Layout.alignment: Qt.AlignRight
                text: pane.controller.microphoneScanBusy ? "检测中…" : "刷新设备"
                quiet: true
                enabled: !pane.settingsDialog.deviceSettingsLocked && !pane.controller.microphoneScanBusy
                onClicked: pane.controller.refreshMicrophones()
            }
        }
        SettingsFormRow {
            title: "语音启停方式"
            descriptionObjectName: "speechControlModeHint"
            description: pane.controller.speechControlMode === "gesture"
                ? "开启语音识别后，" + pane.controller.confirmGestureHint + " 开始，再做一次结束。"
                : "靠近说话开始，" + pane.controller.confirmGestureHint + " 结束。"
                  + (pane.controller.audioSource === "microphone" ? "使用选定麦克风检测靠近。" : "")
            SettingsSelect {
                objectName: "speechControlModeCombo"
                Layout.fillWidth: true
                Accessible.name: "语音启停方式"
                model: ["靠近说话", "手势启停"]
                enabled: !pane.settingsDialog.deviceSettingsLocked
                currentIndex: pane.controller.speechControlMode === "gesture" ? 1 : 0
                onActivated: pane.controller.speechControlMode = currentIndex === 1 ? "gesture" : "proximity"
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
        description: "查看语音输入和应用操作所需的系统状态。"
        SettingsFormRow {
            title: "辅助功能"
            description: pane.controller.inlineInput.permissions.title
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
            description: pane.controller.inlineInput.permissions.screenRecordingStatus
            UiAction {
                objectName: "settingsCategory6"
                Layout.alignment: Qt.AlignRight
                text: "查看与授权"
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
        SettingsCategoryButton {
            objectName: "advancedSettingsButton"
            Layout.fillWidth: true
            text: "高级设置"
            description: "识别服务、文本模型与性能"
            onClicked: pane.advancedSettingsRequested()
        }
    }
}
