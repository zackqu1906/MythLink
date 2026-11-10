import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

ColumnLayout {
    id: pane
    required property var controller
    signal audioRequested()
    signal gesturesRequested()
    readonly property real permissionsPosition: permissionsGroup.y
    signal generalRequested()
    signal agentRequested()
    signal browserRequested()
    signal advancedRequested()
    signal logRequested()
    spacing: 32
    FigmaTheme { id: theme }
    PreferenceSection {
        Layout.fillWidth: true; title: "通用"
        PreferenceRow {
            Layout.fillWidth: true; title: "开机启动"
            description: pane.controller.startup.error || "登录电脑后启动 MythLink。"
            Item { Layout.fillWidth: true }
            PreferenceSwitch {
                objectName: "launchAtLoginSwitch"
                checked: pane.controller.startup.enabled
                enabled: pane.controller.startup.supported
                Accessible.name: "开机启动"
                onClicked: pane.controller.startup.setEnabled(checked)
            }
        }
        PreferenceRow {
            Layout.fillWidth: true; title: "界面语言"; description: "影响整个应用的显示语言。"
            FigmaSelect {
                objectName: "interfaceLanguageSelect"
                Layout.fillWidth: true; model: ["简体中文"]; font.pixelSize: 16; font.weight: Font.Medium
                leftPadding: 16; Accessible.name: "界面语言"
            }
        }
    }
    PreferenceSection {
        Layout.fillWidth: true; title: "功能偏好"
        PreferenceRow {
            Layout.fillWidth: true; title: "语音输入设备"
            description: pane.controller.microphoneDevicesError || "选择电脑麦克风时，Ring 仅提供手势，无需内置麦克风。"
            AudioInputSelect {
                objectName: "settingsVoiceInputDeviceCombo"
                Layout.fillWidth: true
                controller: pane.controller
            }
        }
        UiNotice {
            objectName: "settingsVoiceInputDeviceStatus"
            Layout.fillWidth: true
            visible: text.length > 0
            text: pane.controller.audioSettingsStatus
            warning: pane.controller.audioSettingsError
            font.pixelSize: 13; wrapMode: Text.Wrap
        }
        PreferenceRow {
            Layout.fillWidth: true; title: "听写偏好"; description: "麦克风、输入方式、语音快捷键与个性化。"; linked: true
            FigmaAction { objectName: "settingsAudioEntry"; text: "前往设置"; navigation: true; implicitHeight: 32; implicitWidth: 100; font.pixelSize: 14; leftPadding: 8; rightPadding: 8; onClicked: pane.audioRequested() }
        }
        PreferenceRow {
            Layout.fillWidth: true; title: "手势映射"; description: "全局与应用手势统一在戒指模块中编辑。"; linked: true
            FigmaAction { objectName: "settingsGesturesEntry"; text: "前往设置"; navigation: true; implicitHeight: 32; implicitWidth: 100; font.pixelSize: 14; leftPadding: 8; rightPadding: 8; onClicked: pane.gesturesRequested() }
        }
    }
    PreferenceSection {
        Layout.fillWidth: true; title: "数据与分析"
        PreferenceRow {
            objectName: "voiceHistoryExportRow"
            Layout.fillWidth: true; title: "语音历史记录"
            description: "导出全部记录、上下文、录音和截图到下载文件夹，附带便于分析的统计表。"
            FigmaAction {
                objectName: "exportVoiceHistoryButton"
                Layout.fillWidth: true
                text: pane.controller.voiceHistoryExport.busy ? "正在导出…" : "一键导出"
                enabled: !pane.controller.voiceHistoryExport.busy
                onClicked: pane.controller.voiceHistoryExport.export()
            }
        }
        UiNotice {
            objectName: "voiceHistoryExportStatus"
            Layout.fillWidth: true
            visible: text.length > 0
            text: pane.controller.voiceHistoryExport.message
            warning: pane.controller.voiceHistoryExport.error
            wrapMode: Text.Wrap
        }
        FigmaAction {
            objectName: "openVoiceHistoryExportFolderButton"
            visible: pane.controller.voiceHistoryExport.path.length > 0
            text: "打开导出文件夹"; quiet: true
            onClicked: pane.controller.voiceHistoryExport.openFolder()
        }
    }
    PreferenceSection {
        Layout.fillWidth: true; title: "连接与运行"
        PreferenceRow {
            objectName: "diagnosticLogExportRow"
            Layout.fillWidth: true; title: "导出诊断日志"
            description: "包含运行日志、错误堆栈、设备状态，以及相关语音的上下文、录音和截图。"
            ColumnLayout {
                Layout.fillWidth: true
                spacing: 8
                FigmaSelect {
                    id: diagnosticPeriod
                    objectName: "diagnosticLogRangeSelect"
                    Layout.fillWidth: true
                    Accessible.name: "诊断日志时间范围"
                    model: ["今天", "近一周", "全部"]
                    enabled: !pane.controller.diagnosticExport.busy
                }
                FigmaAction {
                    objectName: "settingsExportDiagnosticLogButton"
                    Layout.fillWidth: true
                    text: pane.controller.diagnosticExport.busy ? "正在导出…" : "导出诊断包"
                    enabled: !pane.controller.diagnosticExport.busy
                    onClicked: pane.controller.exportDiagnosticLog(["today", "week", "all"][diagnosticPeriod.currentIndex])
                }
            }
        }
        Label {
            Layout.fillWidth: true; Layout.leftMargin: 16
            text: "近一周包含今天及前六天；全部为本地仍保留的记录。诊断包仅保存到本机。"
            color: theme.muted; font.pixelSize: 13; wrapMode: Text.Wrap
        }
        UiNotice {
            objectName: "diagnosticLogExportStatus"
            Layout.fillWidth: true
            visible: text.length > 0
            text: pane.controller.diagnosticExport.message
            warning: pane.controller.diagnosticExport.error
            wrapMode: Text.Wrap
        }
        FigmaAction {
            objectName: "openDiagnosticExportFolderButton"
            visible: pane.controller.diagnosticExport.path.length > 0
            text: "打开导出文件夹"; quiet: true
            onClicked: pane.controller.diagnosticExport.openFolder()
        }
        PreferenceRow {
            Layout.fillWidth: true; title: "运行日志"; description: "查看最近的实时运行记录。"
            FigmaAction { objectName: "runtimeLogButton"; Layout.fillWidth: true; text: "查看日志"; font.pixelSize: 16; onClicked: pane.logRequested() }
        }
    }
    SettingsPermissions {
        id: permissionsGroup
        Layout.fillWidth: true
        visible: Qt.platform.os === "osx"
        controller: pane.controller
    }
    PreferenceSection {
        Layout.fillWidth: true; title: "更多设置"
        PreferenceRow {
            Layout.fillWidth: true; title: "界面与通用"; description: "手势提示、浮窗大小与菜单栏状态。"; linked: true
            FigmaAction { objectName: "settingsGeneralEntry"; text: "前往设置"; navigation: true; onClicked: pane.generalRequested() }
        }
        PreferenceRow {
            Layout.fillWidth: true; title: "Agent"; description: "连接 Codex，设置任务与推荐偏好。"; linked: true
            FigmaAction { objectName: "settingsAgentEntry"; text: "前往设置"; navigation: true; onClicked: pane.agentRequested() }
        }
        PreferenceRow {
            visible: Qt.platform.os === "osx"
            Layout.fillWidth: true; title: "浏览器"; description: "安装浏览器插件，连接网页媒体控制。"; linked: true
            FigmaAction { objectName: "settingsBrowserEntry"; text: "前往设置"; navigation: true; onClicked: pane.browserRequested() }
        }
        PreferenceRow {
            Layout.fillWidth: true; title: "高级设置"; description: "识别服务、文本模型与性能。"; linked: true
            FigmaAction { objectName: "advancedSettingsButton"; text: "前往设置"; navigation: true; onClicked: pane.advancedRequested() }
        }
    }
}
