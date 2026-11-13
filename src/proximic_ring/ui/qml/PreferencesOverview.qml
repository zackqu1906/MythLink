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
    signal firmwareRequested()
    signal agentRequested()
    signal browserRequested()
    signal advancedRequested()
    signal logRequested()
    signal voiceHistoryRequested()
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
        Layout.fillWidth: true; title: "戒指"
        PreferenceRow {
            Layout.fillWidth: true; title: "戒指固件更新"
            description: "随应用固件：" + pane.controller.firmwareUpdate.bundledVersion
            linked: true
            FigmaAction {
                objectName: "settingsFirmwareEntry"
                text: pane.controller.firmwareUpdate.busy ? "查看更新进度" : "前往更新"
                navigation: true
                onClicked: pane.firmwareRequested()
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
        objectName: "recordsAndExportsSection"
        Layout.fillWidth: true; title: "记录与导出"
        RecordExportRow {
            Layout.fillWidth: true
            title: "语音记录"; description: "按应用导出完整语音记录、上下文及已有录音和截图。"
            exporter: pane.controller.voiceHistoryExport
            rowName: "voiceHistoryExportRow"; viewName: "viewVoiceHistoryButton"
            exportName: "exportVoiceHistoryButton"; statusName: "voiceHistoryExportStatus"
            folderName: "openVoiceHistoryExportFolderButton"
            onViewRequested: pane.voiceHistoryRequested()
            onExportRequested: pane.controller.voiceHistoryExport.export()
        }
        RecordExportRow {
            Layout.fillWidth: true
            title: "手势记录"; description: "查看手势的应用、场景、动作与标注，按应用导出。"
            exporter: pane.controller.gestureHistory
            rowName: "gestureHistoryRow"; viewName: "viewGestureHistoryButton"
            exportName: "exportGestureHistoryButton"; statusName: "gestureHistoryExportStatus"
            folderName: "openGestureHistoryExportFolderButton"
            onViewRequested: pane.controller.gestureHistory.show()
            onExportRequested: pane.controller.gestureHistory.export()
        }
        RecordExportRow {
            Layout.fillWidth: true
            title: "诊断日志"; description: "按时间导出完整运行日志、错误堆栈与设备状态。"
            exporter: pane.controller.diagnosticExport
            rowName: "diagnosticLogExportRow"; viewName: "runtimeLogButton"
            exportName: "settingsExportDiagnosticLogButton"; statusName: "diagnosticLogExportStatus"
            folderName: "openDiagnosticExportFolderButton"
            onViewRequested: pane.logRequested()
            onExportRequested: pane.controller.exportDiagnosticLog("today")
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
