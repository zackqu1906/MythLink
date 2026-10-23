import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

ColumnLayout {
    id: pane
    required property var controller
    required property var settingsDialog
    property string expandedSection: ""
    signal resetScrollRequested()
    onExpandedSectionChanged: resetScrollRequested()
    readonly property real permissionsPosition: overview.y + overview.permissionsPosition
    signal detailRequested(int page)
    signal advancedSettingsRequested()
    signal logRequested()
    spacing: 18
    FigmaTheme { id: theme }
    FigmaAction { objectName: "settingsOverviewBackButton"; visible: pane.expandedSection.length > 0; text: "返回所有设置"; navigation: true; onClicked: pane.expandedSection = "" }
    PreferencesOverview {
        id: overview
        visible: pane.expandedSection.length === 0
        Layout.fillWidth: true
        controller: pane.controller
        onAudioRequested: pane.expandedSection = "audio"
        onGesturesRequested: pane.detailRequested(12)
        onGeneralRequested: pane.expandedSection = "general"
        onAgentRequested: pane.expandedSection = "agent"
        onAdvancedRequested: pane.advancedSettingsRequested()
        onLogRequested: pane.logRequested()
    }

    FigmaFormGroup {
        flat: true
        objectName: "settingsAudioGroup"
        visible: pane.expandedSection === "audio"
        title: "麦克风与语音"; symbol: "microphone"
        description: "连接期间可修改；正在进行的语音处理完成后，再应用新的音频来源与启停方式。"
        UiNotice {
            objectName: "audioSettingsStatus"
            Layout.fillWidth: true
            visible: text.length > 0
            text: pane.controller.audioSettingsStatus
            warning: pane.controller.audioSettingsError
            font.pixelSize: 13; wrapMode: Text.Wrap
        }
        FigmaFormRow {
            preferenceStyle: true
            title: "音频来源"
            description: pane.controller.microphoneDevicesError.length > 0
                ? pane.controller.microphoneDevicesError
                : "直接选择 Ring 或已检测到的音频输入设备。"
            FigmaSelect {
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
            FigmaAction {
                objectName: "refreshMicrophonesButton"
                Layout.alignment: Qt.AlignRight
                text: pane.controller.microphoneScanBusy ? "检测中…" : "刷新设备"
                quiet: true
                enabled: !pane.controller.microphoneScanBusy
                onClicked: pane.controller.refreshMicrophones()
            }
        }
        FigmaFormRow {
            preferenceStyle: true
            title: "语音启停方式"
            descriptionObjectName: "speechControlModeHint"
            description: (pane.controller.audioSettings.speech_control_mode === "gesture"
                ? "开启语音识别后，" + pane.controller.confirmGestureHint + " 开始，" + pane.controller.endGestureHint + " 结束。"
                : "靠近说话开始，" + pane.controller.endGestureHint + " 结束。"
                  + (pane.controller.audioSettings.audio_source === "microphone" ? "使用选定麦克风检测靠近。" : ""))
            FigmaSelect {
                objectName: "speechControlModeCombo"
                Layout.fillWidth: true
                Accessible.name: "语音启停方式"
                model: ["靠近说话", "手势启停"]
                enabled: !pane.controller.busy
                currentIndex: pane.controller.audioSettings.speech_control_mode === "gesture" ? 1 : 0
                onActivated: pane.controller.speechControlMode = currentIndex === 1 ? "gesture" : "proximity"
            }
        }
        FigmaFormRow {
            preferenceStyle: true
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
        FigmaFormRow {
            preferenceStyle: true
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

    FigmaFormGroup {
        flat: true
        objectName: "settingsInputGroup"
        visible: pane.expandedSection === "audio"
        title: "输入与控制"; symbol: "pointer"
        FigmaFormRow {
            preferenceStyle: true
            visible: pane.controller.inlineInput.enabled
            title: "多次撤销"
            description: "逐次撤回当前输入框内的听写和编辑；关闭时只保留最近一次操作。"
            PreferenceSwitch {
                objectName: "multiUndoSwitch"
                Layout.alignment: Qt.AlignRight
                Accessible.name: "多次撤销"
                checked: pane.controller.multiUndoEnabled
                onClicked: pane.controller.multiUndoEnabled = checked
            }
        }
        FigmaFormRow {
            preferenceStyle: true
            title: "类型转换按键"
            description: "仅在当前语音结果可以转换时生效，连接期间也可修改。"
            FigmaSelect {
                objectName: "modeCorrectionShortcutCombo"
                Layout.fillWidth: true
                Accessible.name: "类型转换按键"
                model: pane.controller.modeCorrectionShortcutOptions
                currentIndex: Math.max(0, pane.controller.modeCorrectionShortcutOptions.indexOf(pane.controller.modeCorrectionShortcut))
                onActivated: pane.controller.modeCorrectionShortcut = pane.controller.modeCorrectionShortcutOptions[currentIndex]
            }
        }
        FigmaFormRow {
            preferenceStyle: true
            visible: !pane.controller.inlineInput.enabled
            title: "听写与指令识别"
            description: pane.controller.inputRoutingMode === "auto"
                ? "语音结束后自动判断听写或编辑指令。"
                : "沿用当前选择的听写或编辑模式。"
            FigmaSelect {
                objectName: "inputRoutingModeCombo"
                Layout.fillWidth: true
                Accessible.name: "听写与指令识别"
                model: ["自动判断", "手动选择"]
                currentIndex: pane.controller.inputRoutingMode === "auto" ? 0 : 1
                onActivated: pane.controller.inputRoutingMode = currentIndex === 0 ? "auto" : "manual"
            }
        }
        FigmaFormRow {
            preferenceStyle: true
            visible: !pane.controller.inlineInput.enabled
            title: "整理听写文本"
            description: "关闭后直接使用识别结果；编辑指令仍会使用文本模型。"
            PreferenceSwitch {
                objectName: "dictationLlmSwitch"
                Layout.alignment: Qt.AlignRight
                Accessible.name: "使用大模型整理听写文本"
                checked: pane.controller.llmEnabled
                onClicked: pane.controller.llmEnabled = checked
            }
        }
        FigmaFormRow {
            preferenceStyle: true
            visible: Qt.platform.os === "windows" || Qt.platform.os === "osx"
            title: "输入到当前光标"
            description: "识别完成后，将结果输入到当前文本框。"
            PreferenceSwitch {
                objectName: "desktopOutputSwitch"
                Layout.alignment: Qt.AlignRight
                Accessible.name: "识别完成后输入到当前光标"
                checked: pane.controller.desktopOutputEnabled
                onClicked: pane.controller.desktopOutputEnabled = checked
            }
        }
        FigmaFormRow {
            preferenceStyle: true
            visible: Qt.platform.os === "windows" && pane.controller.speechControlMode !== "gesture"
            title: "右 Alt 按住说话"
            description: "识别开启时，按键优先于自动靠近检测。"
            PreferenceSwitch {
                objectName: "pushToTalkSwitch"
                Layout.alignment: Qt.AlignRight
                Accessible.name: "启用右 Alt 按住说话"
                enabled: !pane.settingsDialog.deviceSettingsLocked && pane.controller.speechControlMode !== "gesture"
                checked: pane.controller.pushToTalkEnabled
                onClicked: pane.controller.pushToTalkEnabled = checked
            }
        }
    }

    FigmaFormGroup {
        flat: true
        objectName: "settingsAgentGroup"
        visible: pane.expandedSection === "agent"
        title: "Agent"; symbol: "operation"
        AgentSetupRows { agent: pane.controller.agent }
    }

    BrowserExtensionSettings {
        visible: pane.expandedSection === "agent"
        bridge: pane.controller.browserExtensions
    }

    FigmaFormGroup {
        flat: true
        objectName: "settingsAppearanceGroup"
        visible: pane.expandedSection === "general"
        title: "界面显示"; symbol: "settings"
        FigmaFormRow {
            preferenceStyle: true
            title: "手势触发提示"
            description: "识别到手势（含 click 和 double-click）时，在屏幕右下角显示提示；新提示将旧提示向上推，每条独立显示 1 秒。"
            PreferenceSwitch {
                objectName: "gestureTriggerHintsSwitch"
                Layout.alignment: Qt.AlignRight
                Accessible.name: "手势触发提示"
                checked: pane.controller.gestureTrigger.enabled
                onClicked: pane.controller.gestureTrigger.enabled = checked
            }
        }
        FigmaFormRow {
            preferenceStyle: true
            visible: pane.controller.inlineInput.enabled
            title: "输入法浮窗大小"
            description: "默认使用小浮窗，调整后立即生效。"
            Slider {
                objectName: "voiceHudScaleSlider"
                Layout.fillWidth: true
                Accessible.name: "输入法浮窗大小"
                from: 80; to: 140; stepSize: 10
                value: pane.controller.voiceHudScale
                onMoved: pane.controller.voiceHudScale = Math.round(value)
            }
            RowLayout {
                Layout.fillWidth: true
                Label {
                    objectName: "voiceHudScaleLabel"
                    Layout.fillWidth: true
                    text: pane.controller.voiceHudScale + "%" + (pane.controller.voiceHudScale === 80 ? "（默认）" : "")
                    color: theme.muted; font.pixelSize: 12
                }
                FigmaAction {
                    objectName: "resetVoiceHudScaleButton"
                    text: "恢复默认"; quiet: true
                    enabled: pane.controller.voiceHudScale !== 80
                    onClicked: pane.controller.voiceHudScale = 80
                }
            }
        }
    }

    FigmaFormGroup {
        flat: true
        objectName: "settingsGeneralGroup"
        visible: pane.expandedSection === "general"
        title: "通用"
        FigmaFormRow {
            preferenceStyle: true
            title: "应用版本"
            descriptionObjectName: "settingsAppVersion"
            description: pane.controller.appVersion
        }
        FigmaFormRow {
            preferenceStyle: true
            visible: Qt.platform.os === "osx"
            title: "菜单栏状态"
            description: "显示连接状态和电量，关闭后只保留应用图标。"
            PreferenceSwitch {
                objectName: "menuBarStatusInfoSwitch"
                Layout.alignment: Qt.AlignRight
                Accessible.name: "显示菜单栏连接状态和电量"
                checked: pane.controller.menuBarStatusInfo
                onClicked: pane.controller.menuBarStatusInfo = checked
            }
        }
        FigmaFormRow {
            preferenceStyle: true
            visible: Qt.platform.os === "osx"
            title: "离开锁屏"
            description: "戒指远离时锁屏，也支持手动锁屏后弹指解锁。"
            SettingsStatusAction {
                preferenceStyle: true
                statusObjectName: "proximityOverviewStatus"
                statusText: pane.controller.proximity.enabled ? "已开启" : "未开启"
                statusReady: pane.controller.proximity.enabled
                buttonObjectName: "settingsCategory9"
                text: "设置"
                onClicked: pane.detailRequested(9)
            }
        }
        FigmaFormRow {
            preferenceStyle: true
            title: "高级设置"
            description: "识别服务、文本模型与性能"
            FigmaAction {
                objectName: "generalAdvancedSettingsButton"
                Layout.alignment: Qt.AlignRight
                text: "高级设置"
                onClicked: pane.advancedSettingsRequested()
            }
        }
    }
}
