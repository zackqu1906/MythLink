import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

ColumnLayout {
    id: pane
    required property var controller
    readonly property bool local: controller.llmProvider === "local"
    readonly property var presetModels: ["doubao-seed-2-0-lite-260215", "deepseek-v4-flash-260425"]
    spacing: 30
    UiTheme { id: theme }

    SettingsFormGroup {
        title: "文本处理"
        description: "用于编辑指令和听写整理，修改在下一次文本处理时生效。"
        SettingsFormRow {
            title: "运行方式"
            description: pane.local ? "文本模型在电脑上运行，语音识别服务单独设置。"
                : "使用已配置的在线文本服务，需要网络连接。"
            SettingsSelect {
                objectName: "llmProviderCombo"
                Layout.fillWidth: true
                Accessible.name: "文本处理运行方式"
                model: ["本地运行", "在线服务"]
                currentIndex: pane.local ? 0 : 1
                onActivated: {
                    if (currentIndex === 0) pane.controller.llmProvider = "local"
                    else if (pane.local) pane.controller.llmProvider = "volcengine"
                }
            }
        }
    }

    SettingsFormGroup {
        objectName: "localTextModelGroup"
        visible: pane.local
        title: "本地模型"
        SettingsFormRow {
            title: pane.controller.localModelInstalled ? "模型已就绪" : "准备本地模型"
            description: pane.controller.localModelInstallStatus
            UiAction {
                objectName: "installLocalModelButton"
                Layout.alignment: Qt.AlignRight
                text: pane.controller.localModelInstalled ? "已安装"
                    : pane.controller.localModelInstalling ? "下载中…" : "下载本地模型"
                primary: !pane.controller.localModelInstalled
                enabled: !pane.controller.localModelInstalled && !pane.controller.localModelInstalling
                onClicked: pane.controller.installLocalModel()
            }
        }
        Label {
            Layout.fillWidth: true
            text: "安装后会在需要处理文本时加载。下载期间可以继续使用应用其他功能。"
            color: theme.muted; font.pixelSize: 13; wrapMode: Text.Wrap
        }
    }

    SettingsFormGroup {
        objectName: "onlineTextModelGroup"
        visible: !pane.local
        title: pane.controller.llmProvider === "volcengine" ? "火山方舟" : "当前在线服务"
        SettingsFormRow {
            title: "在线模型"
            description: "沿用已保存的模型，也可在下方填写自定义模型标识。"
            controlWidth: 340
            SettingsSelect {
                objectName: "llmModelCombo"
                Layout.fillWidth: true
                Accessible.name: "在线文本模型"
                visible: pane.controller.llmProvider === "volcengine"
                model: ["豆包 Seed 2.0", "DeepSeek V4"]
                currentIndex: pane.presetModels.indexOf(pane.controller.llmModel)
                displayText: currentIndex < 0 ? "当前模型：" + pane.controller.llmModel : currentText
                onActivated: pane.controller.llmModel = pane.presetModels[currentIndex]
            }
            Label {
                Layout.fillWidth: true
                visible: pane.controller.llmProvider !== "volcengine"
                text: pane.controller.llmModel || "尚未填写模型"
                color: theme.text; font.pixelSize: 13; wrapMode: Text.Wrap
            }
        }
        SettingsFormRow {
            title: "服务密钥"
            description: pane.controller.llmApiKeyEnv.length
                ? "未填写时沿用已配置的环境变量。"
                : "保存在当前用户的应用设置中。"
            controlWidth: 340
            RowLayout {
                Layout.fillWidth: true; spacing: 8
                UiTextField {
                    objectName: "llmApiKeyField"
                    Layout.fillWidth: true; Layout.minimumWidth: 0
                    text: pane.controller.llmApiKey
                    hintText: pane.controller.llmProvider === "volcengine" ? "填写火山方舟 API Key" : "填写服务 API Key"
                    echoMode: revealLlmKey.checked ? TextInput.Normal : TextInput.Password
                    onEditingFinished: pane.controller.llmApiKey = text
                }
                UiAction {
                    id: revealLlmKey
                    objectName: "showLlmApiKeyButton"
                    checkable: true; quiet: true
                    text: checked ? "隐藏" : "显示"
                    Accessible.name: checked ? "隐藏文本服务密钥" : "显示文本服务密钥"
                    onVisibleChanged: if (!visible) checked = false
                }
            }
        }
    }

    SettingsFormGroup {
        objectName: "llmAdvancedGroup"
        visible: !pane.local
        title: "在线服务参数"
        description: "仅在使用自定义在线服务时调整，需与服务提供方的配置一致。"
        SettingsFormRow {
            title: "服务地址"; controlWidth: 340
            UiTextField {
                objectName: "llmBaseUrlField"
                Layout.fillWidth: true
                text: pane.controller.llmBaseUrl
                hintText: "填写服务 API 地址"
                onEditingFinished: pane.controller.llmBaseUrl = text
            }
        }
        SettingsFormRow {
            title: "模型标识"; controlWidth: 340
            UiTextField {
                objectName: "llmModelField"
                Layout.fillWidth: true
                text: pane.controller.llmModel
                hintText: "填写模型或接入点标识"
                onEditingFinished: pane.controller.llmModel = text
            }
        }
    }
}
