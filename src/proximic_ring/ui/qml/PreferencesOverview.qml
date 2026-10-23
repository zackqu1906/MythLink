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
            Layout.fillWidth: true; title: "听写偏好"; description: "麦克风、输入方式、语音快捷键与个性化。"; linked: true
            FigmaAction { objectName: "settingsAudioEntry"; text: "前往设置"; navigation: true; implicitHeight: 32; implicitWidth: 100; font.pixelSize: 14; leftPadding: 8; rightPadding: 8; onClicked: pane.audioRequested() }
        }
        PreferenceRow {
            Layout.fillWidth: true; title: "手势映射"; description: "全局与应用手势统一在戒指模块中编辑。"; linked: true
            FigmaAction { objectName: "settingsGesturesEntry"; text: "前往设置"; navigation: true; implicitHeight: 32; implicitWidth: 100; font.pixelSize: 14; leftPadding: 8; rightPadding: 8; onClicked: pane.gesturesRequested() }
        }
    }
    PreferenceSection {
        Layout.fillWidth: true; title: "连接与运行"
        PreferenceRow {
            Layout.fillWidth: true; title: "运行日志"; description: "查看实时日志，导出记录以便排查问题。"
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
            Layout.fillWidth: true; title: "Agent 与浏览器"; description: "连接 Agent 与浏览器扩展。"; linked: true
            FigmaAction { objectName: "settingsAgentEntry"; text: "前往设置"; navigation: true; onClicked: pane.agentRequested() }
        }
        PreferenceRow {
            Layout.fillWidth: true; title: "高级设置"; description: "识别服务、文本模型与性能。"; linked: true
            FigmaAction { objectName: "advancedSettingsButton"; text: "前往设置"; navigation: true; onClicked: pane.advancedRequested() }
        }
    }
}
