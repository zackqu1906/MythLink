import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

ColumnLayout {
    id: pane
    required property var agent
    Layout.fillWidth: true
    spacing: 18
    FigmaTheme { id: theme }
    FigmaFormRow {
            preferenceStyle: true
        objectName: "agentCodexRow"
        title: "连接 Codex"
        descriptionObjectName: "agentConnectionMessage"
        description: pane.agent.connectionMessage
        SettingsStatusAction {
                preferenceStyle: true
            statusObjectName: "agentConnectionStatus"
            statusText: pane.agent.connectionStatus
            statusReady: pane.agent.connectionReady
            buttonObjectName: "agentConnectButton"
            text: pane.agent.connectionChecking ? "检查中…"
                : pane.agent.connectionMissing ? "安装 Codex"
                : pane.agent.connectionReady ? "检查连接" : "连接 Codex"
            buttonEnabled: !pane.agent.connectionChecking && !pane.agent.busy
            onClicked: {
                if (pane.agent.connectionMissing) pane.agent.openCodex()
                else pane.agent.connectCodex()
            }
        }
        FigmaAction {
            objectName: "agentConnectionHelpButton"
            Layout.alignment: Qt.AlignRight
            visible: pane.agent.connectionNeedsHelp && !pane.agent.connectionMissing
            text: pane.agent.connectionHelpLabel
            onClicked: pane.agent.openCodex()
        }
    }
    FigmaFormRow {
            preferenceStyle: true
        objectName: "agentEmbeddingRow"
        visible: pane.agent.workflowPipelineEnabled
        title: "语义召回模型"
        descriptionObjectName: "agentEmbeddingMessage"
        description: pane.agent.setup.status
        SettingsStatusAction {
                preferenceStyle: true
            statusObjectName: "agentEmbeddingStatus"
            statusText: pane.agent.setup.busy ? "处理中"
                : pane.agent.setup.ready ? "已下载" : "未下载"
            statusReady: pane.agent.setup.ready && !pane.agent.setup.busy
            buttonObjectName: "agentEmbeddingDownloadButton"
            text: pane.agent.setup.busy ? "取消" : pane.agent.setup.actionLabel
            onClicked: {
                if (pane.agent.setup.busy) pane.agent.setup.cancel()
                else pane.agent.setup.download()
            }
        }
        ProgressBar {
            objectName: "agentEmbeddingProgress"
            Layout.fillWidth: true
            visible: pane.agent.setup.busy
            value: pane.agent.setup.progress
            padding: 0
            implicitHeight: 4
            background: Rectangle { radius: 2; color: theme.line }
            contentItem: Item {
                Rectangle {
                    width: parent.width * pane.agent.setup.progress
                    height: parent.height
                    radius: 2; color: theme.primary
                }
            }
        }
    }
    FigmaFormRow {
            preferenceStyle: true
        objectName: "agentHistoryRecommendationRow"
        title: "下一步推荐"
        description: pane.agent.historyRecommendations.status
            + "。使用 Codex Computer History，在鼠标旁显示最多四项建议。"
        PreferenceSwitch {
            objectName: "agentHistoryRecommendationSwitch"
            Layout.alignment: Qt.AlignRight
            Accessible.name: "根据近期电脑活动推荐下一步"
            checked: pane.agent.historyRecommendations.enabled
            onClicked: pane.agent.historyRecommendations.enabled = checked
        }
    }
    FigmaFormRow {
            preferenceStyle: true
        objectName: "agentWorkflowAutoRow"
        title: "自动整理任务"
        description: pane.agent.workflowPipelineEnabled
            ? "使用已配置的文本服务，在任务结束后后台整理任务记录。"
            : "Workflow 及旧推荐流程已停用，历史记录仍可查看。"
        PreferenceSwitch {
            objectName: "agentWorkflowAutoSwitch"
            enabled: pane.agent.workflowPipelineEnabled
            Layout.alignment: Qt.AlignRight
            Accessible.name: "自动整理任务"
            checked: pane.agent.workflowAuto
            onClicked: pane.agent.workflowAuto = checked
        }
    }
}
