import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

ColumnLayout {
    id: panel
    objectName: "automaticVoiceDetails"
    required property var details
    signal closeRequested()
    spacing: 8
    VoiceHistoryTheme { id: theme }
    Rectangle {
        objectName: "automaticVoiceRoutingWarning"
        Layout.fillWidth: true; implicitHeight: warningLabel.implicitHeight + 20
        visible: Boolean(panel.details.warningText)
        color: theme.warningSurface; border.color: theme.warningBorder; radius: 8
        Label {
            id: warningLabel
            anchors.fill: parent; anchors.margins: 10
            text: panel.details.warningText || ""
            color: theme.warning; font.family: theme.family; font.pixelSize: theme.detailSize
            wrapMode: Text.Wrap; textFormat: Text.PlainText
        }
    }
    Repeater {
        model: [
            {title: "总体意图", value: panel.details.level1Text},
            {title: "编辑任务", value: panel.details.level2Text},
            {title: "旧记录辅助标签", value: panel.details.hintText},
            {title: "路由模型", value: panel.details.modelText},
            {title: "耗时", value: panel.details.timingText},
            {title: "判断异常", value: panel.details.errorText},
            {title: "截图上文", value: panel.details.contextText}
        ]
        delegate: Label {
            required property var modelData
            Layout.fillWidth: true; visible: Boolean(modelData.value)
            text: modelData.title + "：" + (modelData.value || "")
            color: theme.muted; font.family: theme.family; font.pixelSize: theme.detailSize
            wrapMode: Text.Wrap; textFormat: Text.PlainText
        }
    }
    Image {
        id: screenshot
        objectName: "automaticVoiceScreenshot"
        Layout.fillWidth: true; Layout.preferredHeight: panel.details.screenshotUrl ? 230 : 0
        visible: Boolean(panel.details.screenshotUrl)
        source: panel.details.screenshotUrl || ""
        asynchronous: true; cache: false
        fillMode: Image.PreserveAspectFit
        Rectangle {
            // Geometry uses the original screenshot's normalized top-left coordinates.
            readonly property var region: panel.details.layout || ({})
            visible: screenshot.status === Image.Ready && region.sidebar !== undefined
            x: (screenshot.width - screenshot.paintedWidth) / 2 + Number(region.sidebar || 0) * screenshot.paintedWidth
            y: (screenshot.height - screenshot.paintedHeight) / 2
            width: (1 - Number(region.sidebar || 0)) * screenshot.paintedWidth
            height: Number(region.history_bottom === undefined ? 1 : region.history_bottom) * screenshot.paintedHeight
            color: "transparent"; border.width: 2; border.color: "#D97706"
        }
    }
    Label {
        objectName: "automaticVoiceScreenshotCaption"
        Layout.fillWidth: true
        text: panel.details.screenshotText + (screenshot.status === Image.Error ? "；图片读取失败" : "")
              + (panel.details.screenshotUrl && panel.details.layout.sidebar !== undefined ? "。橙框为 OCR 区域，标题等文字随后按规则过滤。" : "")
        color: theme.muted; font.family: theme.family; font.pixelSize: theme.detailSize
        wrapMode: Text.Wrap; textFormat: Text.PlainText
    }
    FigmaAction {
        visible: Boolean(panel.details.screenshotUrl)
        text: "打开原图"; quiet: true
        onClicked: Qt.openUrlExternally(panel.details.screenshotUrl)
    }
    Label {
        objectName: "automaticVoiceOcrText"
        Layout.fillWidth: true; visible: Boolean(panel.details.ocrText)
        text: "过滤后上文：\n" + (panel.details.ocrText || "")
        color: theme.text; font.family: theme.family; font.pixelSize: theme.detailSize
        wrapMode: Text.Wrap; textFormat: Text.PlainText
    }
    Repeater {
        model: [
            {name: "automaticVoiceRouterOcr", title: "Clef 路由 OCR", status: panel.details.routerOcrStatus, value: panel.details.routerOcrText},
            {name: "automaticVoiceGenerationOcr", title: panel.details.generationOcrTitle, status: panel.details.generationOcrStatus, value: panel.details.generationOcrText}
        ]
        delegate: ColumnLayout {
            required property var modelData
            Layout.fillWidth: true
            Label {
                Layout.fillWidth: true; text: parent.modelData.title || "听写整理／编辑生成 OCR"
                color: theme.text; font.family: theme.family; font.pixelSize: theme.detailSize
                wrapMode: Text.Wrap; textFormat: Text.PlainText
            }
            Label {
                Layout.fillWidth: true; text: parent.modelData.status || "未记录"
                color: theme.muted; font.family: theme.family; font.pixelSize: theme.detailSize
                wrapMode: Text.Wrap; textFormat: Text.PlainText
            }
            ScrollView {
                Layout.fillWidth: true; Layout.preferredHeight: Math.min(220, modelOcrText.implicitHeight + 16)
                clip: true; contentWidth: availableWidth
                TextArea {
                    id: modelOcrText
                    objectName: modelData.name
                    text: modelData.value || "未记录"; readOnly: true; selectByMouse: true
                    color: theme.text; font.family: theme.family; font.pixelSize: theme.detailSize
                    wrapMode: TextEdit.Wrap; textFormat: TextEdit.PlainText
                }
            }
        }
    }
    Repeater {
        model: [
            {title: "各任务概率（每项独立判断启用 / 不启用）", value: panel.details.probabilityText},
            {title: "OCR 原始识别与规则处理", value: panel.details.rawOcrText},
            {title: "实际分类输入与规则", value: panel.details.routerState},
            {title: "文本模型系统提示词", value: panel.details.systemPrompt},
            {title: "文本模型请求内容", value: panel.details.modelInput}
        ]
        delegate: ColumnLayout {
            required property var modelData
            Layout.fillWidth: true; visible: Boolean(modelData.value)
            Label {
                Layout.fillWidth: true; text: parent.modelData.title
                color: theme.muted; font.family: theme.family; font.pixelSize: theme.detailSize
                wrapMode: Text.Wrap; textFormat: Text.PlainText
            }
            ScrollView {
                Layout.fillWidth: true; Layout.preferredHeight: Math.min(220, evidenceText.implicitHeight + 16)
                clip: true; contentWidth: availableWidth
                TextArea {
                    id: evidenceText
                    text: modelData.value || ""; readOnly: true; selectByMouse: true
                    color: theme.text; font.family: theme.family; font.pixelSize: theme.detailSize
                    wrapMode: TextEdit.Wrap; textFormat: TextEdit.PlainText
                }
            }
        }
    }
    FigmaAction {
        objectName: "automaticVoiceDetailsClose"
        text: "关闭完整记录"; quiet: true
        onClicked: panel.closeRequested()
    }
}
