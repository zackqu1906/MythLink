pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

ColumnLayout {
    id: pane
    required property var controller
    property bool deviceSettingsLocked: false
    readonly property bool voiceSettingsLocked: deviceSettingsLocked
        && (controller.voiceRuntimeReady || controller.voiceRetryPending || controller.busy)
    readonly property bool bailian: controller.asrBackend === "bailian"
    readonly property bool online: bailian || controller.asrBackend === "volcengine"
    readonly property var backendIds: ["streaming_sensevoice", "funasr_nano", "volcengine", "bailian"]
    readonly property var bailianModels: ["qwen-audio-3.1-asr-flash-streaming", "qwen-audio-3.1-asr-flash-message"]
    readonly property bool local: ["streaming_sensevoice", "funasr_nano"].indexOf(controller.asrBackend) >= 0
    signal gpuSetupRequested()
    spacing: 18
    FigmaTheme { id: theme }

    component ServiceKeyRow: FigmaFormRow {
        id: keyRow
        required property string fieldName
        required property string buttonName
        required property string hint
        required property string savedKey
        signal saved(string value)
        preferenceStyle: true
        title: "语音服务密钥"
        controlWidth: pane.width >= 760 ? 340 : 220
        RowLayout {
            Layout.fillWidth: true; spacing: 8
            enabled: !pane.voiceSettingsLocked
            FigmaTextField {
                objectName: keyRow.fieldName
                Layout.fillWidth: true; Layout.minimumWidth: 0
                text: keyRow.savedKey
                hintText: keyRow.hint
                echoMode: revealKey.checked ? TextInput.Normal : TextInput.Password
                onEditingFinished: keyRow.saved(text)
            }
            FigmaAction {
                id: revealKey
                objectName: keyRow.buttonName
                checkable: true; quiet: true
                text: checked ? "隐藏" : "显示"
                Accessible.name: checked ? "隐藏语音服务密钥" : "显示语音服务密钥"
                onVisibleChanged: if (!visible) checked = false
            }
        }
    }

    FigmaFormGroup {
        flat: true
        title: "语音识别"; symbol: "microphone"
        description: pane.controller.voiceRuntimeReady ? "断开戒指后可更改识别服务与语言。" : ""
        FigmaFormRow {
            preferenceStyle: true
            title: "识别服务"
            description: pane.bailian ? "通过阿里云百炼识别，边说边显示文字。"
                : pane.online ? "通过火山引擎在线识别，需要网络连接。"
                : pane.controller.asrBackend === "funasr_nano" ? "在电脑上识别，首次使用会下载模型。"
                : pane.controller.asrBackend === "streaming_sensevoice" ? "在电脑上实时识别，缺少模型时需下载约 936 MB。"
                : "沿用已保存的识别配置。"
            FigmaSelect {
                objectName: "asrBackendCombo"
                Layout.fillWidth: true
                Accessible.name: "识别服务"
                enabled: !pane.voiceSettingsLocked
                model: ["实时识别（推荐）", "本地高精度", "豆包（火山引擎）", "千问（阿里云百炼）"]
                currentIndex: pane.backendIds.indexOf(pane.controller.asrBackend)
                displayText: currentIndex < 0 ? "当前配置：" + pane.controller.asrBackend : currentText
                onActivated: pane.controller.asrBackend = pane.backendIds[currentIndex]
            }
        }
        FigmaFormRow {
            preferenceStyle: true
            title: "识别语言"
            description: "不确定时可选择自动识别。"
            FigmaSelect {
                objectName: "asrLanguageCombo"
                Layout.fillWidth: true
                Accessible.name: "识别语言"
                enabled: !pane.voiceSettingsLocked
                model: ["中文", "自动", "英语", "粤语", "日语", "韩语"]
                currentIndex: ["zh", "auto", "en", "yue", "ja", "ko"].indexOf(pane.controller.asrLanguage)
                displayText: currentIndex < 0 ? pane.controller.asrLanguage : currentText
                onActivated: pane.controller.asrLanguage = ["zh", "auto", "en", "yue", "ja", "ko"][currentIndex]
            }
        }
    }

    FigmaFormGroup {
        flat: true
        objectName: "asrOnlineGroup"
        visible: pane.online
        title: "在线服务"
        description: "服务密钥保存在当前用户的应用设置中。"
        FigmaFormRow {
            preferenceStyle: true
            visible: pane.bailian
            title: "识别模型"
            description: "连续转写适合实时听写，短句输入适合语音消息。"
            FigmaSelect {
                objectName: "bailianModelCombo"
                Layout.fillWidth: true
                Accessible.name: "百炼识别模型"
                enabled: !pane.voiceSettingsLocked
                model: ["Qwen-Audio 3.1 · 连续转写", "Qwen-Audio 3.1 · 短句输入"]
                currentIndex: pane.bailianModels.indexOf(pane.controller.asrModel)
                onActivated: pane.controller.asrModel = pane.bailianModels[currentIndex]
            }
        }
        FigmaFormRow {
            preferenceStyle: true
            visible: pane.bailian
            title: "API Host"
            description: "填写百炼控制台中与密钥对应的 API Host。"
            controlWidth: pane.width >= 760 ? 340 : 220
            FigmaTextField {
                objectName: "bailianApiHostField"
                Layout.fillWidth: true
                Accessible.name: "百炼 API Host"
                enabled: !pane.voiceSettingsLocked
                text: pane.controller.bailianApiHost
                hintText: "默认使用北京公共端点"
                onEditingFinished: pane.controller.bailianApiHost = text
            }
        }
        ServiceKeyRow {
            visible: pane.controller.asrBackend === "volcengine"
            fieldName: "asrApiKeyField"
            buttonName: "showAsrApiKeyButton"
            description: "填写豆包语音的 App Key。"
            hint: "填写豆包语音 App Key"
            savedKey: pane.controller.asrApiKey
            onSaved: value => pane.controller.asrApiKey = value
        }
        ServiceKeyRow {
            visible: pane.bailian
            fieldName: "bailianApiKeyField"
            buttonName: "showBailianApiKeyButton"
            description: "留空时使用环境变量 DASHSCOPE_API_KEY。"
            hint: "填写百炼 API Key"
            savedKey: pane.controller.bailianApiKey
            onSaved: value => pane.controller.bailianApiKey = value
        }
    }

    ColumnLayout {
        id: download
        readonly property var progress: pane.controller.asrDownload
        readonly property real received: progress.received || 0
        readonly property real total: progress.total || 0
        Layout.fillWidth: true
        visible: pane.controller.asrBackend === "streaming_sensevoice" && progress.received !== undefined
        spacing: 6
        Label {
            id: downloadLabel
            Layout.fillWidth: true
            wrapMode: Text.Wrap
            color: theme.text
            text: download.total > 0 && download.received >= download.total ? "正在校验 SenseVoice 模型…"
                : download.total > 0 ? "正在下载 SenseVoice 模型　%1%（%2 / %3 MB）"
                    .arg(Math.floor(download.received * 100 / download.total))
                    .arg((download.received / 1000000).toFixed(1)).arg((download.total / 1000000).toFixed(1))
                : download.received > 0 ? "正在下载 SenseVoice 模型　已下载 %1 MB".arg((download.received / 1000000).toFixed(1))
                : "正在获取 SenseVoice 模型文件信息…"
        }
        ProgressBar {
            objectName: "senseVoiceDownloadProgress"
            Layout.fillWidth: true
            Accessible.name: downloadLabel.text
            indeterminate: download.visible && download.total === 0
            value: download.total > 0 ? download.received / download.total : 0
        }
    }
    UiNotice {
        Layout.fillWidth: true
        visible: !download.visible && pane.controller.connected && (pane.controller.voiceRetryPending || pane.controller.voiceRuntimeError.length > 0)
        text: pane.controller.voiceRetryPending ? pane.controller.statusDetail : pane.controller.voiceRuntimeError
        warning: !pane.controller.voiceRetryPending
    }
    FigmaAction {
        objectName: "enableConfiguredVoiceButton"
        Layout.alignment: Qt.AlignRight
        visible: pane.controller.connected && !pane.controller.voiceRuntimeReady
        enabled: !pane.voiceSettingsLocked && !pane.controller.touchpad.blocksVoice
        text: pane.controller.voiceRetryPending ? "正在准备语音…" : "启用语音识别"
        primary: true
        onClicked: {
            pane.forceActiveFocus()
            pane.controller.enableConfiguredVoice()
        }
    }

    FigmaFormGroup {
        flat: true
        objectName: "asrHotwordsGroup"
        visible: pane.controller.asrBackend === "funasr_nano"
        title: "识别热词"
        description: "添加常用人名或专有名词，每行一个；也支持逗号、分号分隔。"
        UiScrollView {
            ScrollBar.vertical.policy: ScrollBar.AlwaysOff
            id: hotwordsScroll
            Layout.fillWidth: true; Layout.preferredHeight: 116
            enabled: !pane.voiceSettingsLocked
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

    FigmaFormGroup {
        flat: true
        objectName: "asrAdvancedGroup"
        title: "本地识别性能"
        visible: pane.local
        FigmaFormRow {
            preferenceStyle: true
            objectName: "asrPerformanceRow"
            visible: pane.local
            title: "本地识别性能"
            description: pane.controller.gpuStatusText
            FigmaSelect {
                objectName: "asrDeviceCombo"
                Layout.fillWidth: true
                Accessible.name: "本地识别性能"
                visible: pane.controller.computeDevices.length > 1
                enabled: !pane.voiceSettingsLocked
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
            FigmaAction {
                objectName: "gpuInstallButton"
                Layout.alignment: Qt.AlignRight
                text: "安装 NVIDIA 加速"
                visible: pane.controller.gpuInstallerAvailable
                enabled: !pane.voiceSettingsLocked
                onClicked: pane.gpuSetupRequested()
            }
        }
    }
}
