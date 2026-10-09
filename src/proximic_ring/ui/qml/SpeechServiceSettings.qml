import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

ColumnLayout {
    id: pane
    required property var controller
    property bool deviceSettingsLocked: false
    readonly property bool online: controller.asrBackend === "volcengine"
    readonly property bool local: ["streaming_sensevoice", "funasr_nano"].indexOf(controller.asrBackend) >= 0
    signal gpuSetupRequested()
    spacing: 30
    UiTheme { id: theme }

    SettingsFormGroup {
        title: "语音识别"; symbol: "microphone"
        description: pane.deviceSettingsLocked
            ? "设备已连接，灰色选项需在首页断开戒指后修改。"
            : "识别服务与语言在下次连接戒指时生效。"
        SettingsFormRow {
            title: "识别服务"
            description: pane.online ? "通过火山引擎在线识别，需要网络连接。"
                : pane.controller.asrBackend === "funasr_nano" ? "在电脑上识别，支持添加常用人名和专有名词。"
                : pane.controller.asrBackend === "streaming_sensevoice" ? "在电脑上实时识别语音。"
                : "沿用已保存的识别配置。"
            SettingsSelect {
                objectName: "asrBackendCombo"
                Layout.fillWidth: true
                Accessible.name: "识别服务"
                enabled: !pane.deviceSettingsLocked
                model: ["实时识别（推荐）", "本地高精度", "在线识别"]
                currentIndex: ["streaming_sensevoice", "funasr_nano", "volcengine"].indexOf(pane.controller.asrBackend)
                displayText: currentIndex < 0 ? "当前配置：" + pane.controller.asrBackend : currentText
                onActivated: pane.controller.asrBackend = ["streaming_sensevoice", "funasr_nano", "volcengine"][currentIndex]
            }
        }
        SettingsFormRow {
            title: "识别语言"
            description: "不确定时可选择自动识别。"
            SettingsSelect {
                objectName: "asrLanguageCombo"
                Layout.fillWidth: true
                Accessible.name: "识别语言"
                enabled: !pane.deviceSettingsLocked
                model: ["中文", "自动", "英语", "粤语", "日语", "韩语"]
                currentIndex: ["zh", "auto", "en", "yue", "ja", "ko"].indexOf(pane.controller.asrLanguage)
                displayText: currentIndex < 0 ? pane.controller.asrLanguage : currentText
                onActivated: pane.controller.asrLanguage = ["zh", "auto", "en", "yue", "ja", "ko"][currentIndex]
            }
        }
    }

    SettingsFormGroup {
        objectName: "asrOnlineGroup"
        visible: pane.online
        title: "在线服务"
        description: "服务密钥保存在当前用户的应用设置中。"
        SettingsFormRow {
            title: "语音服务密钥"
            description: "填写豆包语音的 App Key。"
            controlWidth: 340
            RowLayout {
                Layout.fillWidth: true; spacing: 8
                enabled: !pane.deviceSettingsLocked
                UiTextField {
                    objectName: "asrApiKeyField"
                    Layout.fillWidth: true; Layout.minimumWidth: 0
                    text: pane.controller.asrApiKey
                    hintText: "填写豆包语音 App Key"
                    echoMode: revealAsrKey.checked ? TextInput.Normal : TextInput.Password
                    onEditingFinished: pane.controller.asrApiKey = text
                }
                UiAction {
                    id: revealAsrKey
                    objectName: "showAsrApiKeyButton"
                    checkable: true; quiet: true
                    text: checked ? "隐藏" : "显示"
                    Accessible.name: checked ? "隐藏语音服务密钥" : "显示语音服务密钥"
                    onVisibleChanged: if (!visible) checked = false
                }
            }
        }
    }

    SettingsFormGroup {
        objectName: "asrHotwordsGroup"
        visible: pane.controller.asrBackend === "funasr_nano"
        title: "识别热词"
        description: "添加常用人名或专有名词，每行一个；也支持逗号、分号分隔。重新连接戒指后生效。"
        ScrollView {
            ScrollBar.vertical.policy: ScrollBar.AlwaysOff
            id: hotwordsScroll
            Layout.fillWidth: true; Layout.preferredHeight: 116
            enabled: !pane.deviceSettingsLocked
            clip: true
            ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
            UiTextArea {
                objectName: "asrHotwordsField"
                width: hotwordsScroll.availableWidth
                text: pane.controller.asrHotwords
                hintText: "每行输入一个热词"
                onActiveFocusChanged: if (!activeFocus) pane.controller.asrHotwords = text
            }
        }
    }

    SettingsFormGroup {
        objectName: "asrAdvancedGroup"
        title: "本地识别性能"
        visible: pane.local
        SettingsFormRow {
            objectName: "asrPerformanceRow"
            visible: pane.local
            title: "本地识别性能"
            description: pane.controller.gpuStatusText
            SettingsSelect {
                objectName: "asrDeviceCombo"
                Layout.fillWidth: true
                Accessible.name: "本地识别性能"
                visible: pane.controller.computeDevices.length > 1
                enabled: !pane.deviceSettingsLocked
                model: pane.controller.computeDevices
                textRole: "label"; valueRole: "value"
                currentIndex: indexOfValue(pane.controller.asrDevice)
                onActivated: pane.controller.asrDevice = currentValue
            }
            Label {
                Layout.fillWidth: true
                visible: pane.controller.computeDevices.length <= 1
                text: pane.controller.computeDevices.length ? pane.controller.computeDevices[0].label : "无可用运行设备"
                color: theme.muted; font.pixelSize: 13; wrapMode: Text.Wrap
                horizontalAlignment: Text.AlignRight
            }
            UiAction {
                objectName: "gpuInstallButton"
                Layout.alignment: Qt.AlignRight
                text: "安装 NVIDIA 加速"
                visible: pane.controller.gpuInstallerAvailable
                enabled: !pane.deviceSettingsLocked
                onClicked: pane.gpuSetupRequested()
            }
        }
    }
}
