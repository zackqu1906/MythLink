import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

ColumnLayout {
    id: panel
    objectName: "voiceHistoryDetails"
    required property var details
    spacing: 14
    VoiceHistoryTheme { id: theme }
    Rectangle { Layout.fillWidth: true; implicitHeight: 1; color: theme.line }
    Label {
        objectName: "voiceHistoryDetailsWarning"
        Layout.fillWidth: true; visible: Boolean(panel.details.warningText)
        text: panel.details.warningText || ""; padding: 10
        color: theme.warning; font.family: theme.family; font.pixelSize: theme.detailSize
        wrapMode: Text.Wrap; textFormat: Text.PlainText
        background: Rectangle { color: theme.warningSurface; border.color: theme.warningBorder; radius: 6 }
    }
    Label {
        Layout.fillWidth: true
        text: panel.details.error || (panel.details.summary || "记录详情")
        color: panel.details.error ? theme.warning : theme.text
        font.family: theme.family; font.pixelSize: theme.bodySize; font.weight: Font.DemiBold
        wrapMode: Text.Wrap; textFormat: Text.PlainText
    }
    Label {
        Layout.fillWidth: true; visible: Boolean(panel.details.asrContextStatus)
        text: panel.details.asrContextStatus || ""
        color: theme.muted; font.family: theme.family; font.pixelSize: theme.detailSize
        wrapMode: Text.Wrap; textFormat: Text.PlainText
    }
    Image {
        objectName: "voiceHistoryScreenshot"
        Layout.fillWidth: true; Layout.preferredHeight: visible ? 260 : 0
        visible: Boolean(panel.details.screenshotUrl)
        source: panel.details.screenshotUrl || ""
        fillMode: Image.PreserveAspectFit; asynchronous: true; cache: false
    }
    Label {
        Layout.fillWidth: true
        text: panel.details.screenshotText || ""
        color: theme.muted; font.family: theme.family; font.pixelSize: theme.detailSize
        wrapMode: Text.Wrap; textFormat: Text.PlainText
    }
    FigmaAction {
        text: "打开原图"; quiet: true; visible: Boolean(panel.details.screenshotUrl)
        onClicked: Qt.openUrlExternally(panel.details.screenshotUrl)
    }
    Repeater {
        model: panel.details.sections || []
        delegate: ColumnLayout {
            id: section
            required property var modelData
            Layout.fillWidth: true; spacing: 6
            Label {
                Layout.fillWidth: true; text: section.modelData.title
                color: theme.text; font.family: theme.family; font.pixelSize: theme.detailSize; font.weight: Font.DemiBold
                wrapMode: Text.Wrap; textFormat: Text.PlainText
            }
            Label {
                Layout.fillWidth: true; visible: Boolean(section.modelData.description)
                text: section.modelData.description || ""
                color: theme.muted; font.family: theme.family; font.pixelSize: theme.detailSize
                wrapMode: Text.Wrap; textFormat: Text.PlainText
            }
            ScrollView {
                id: evidenceScroll
                objectName: "voiceHistoryDetailScroll_" + section.modelData.key
                Layout.fillWidth: true; Layout.minimumWidth: 0
                // Leave a wheel/touchpad lane that belongs to the outer history list.
                Layout.rightMargin: 40
                Layout.preferredHeight: Math.min(280, Math.max(48, evidenceText.implicitHeight + topPadding + bottomPadding))
                clip: true; padding: 1
                contentWidth: availableWidth
                contentHeight: evidenceText.implicitHeight
                ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
                ScrollBar.vertical.policy: ScrollBar.AsNeeded
                background: Rectangle { color: theme.surface; border.color: theme.line; radius: 6 }
                TextArea {
                    id: evidenceText
                    objectName: "voiceHistoryDetail_" + section.modelData.key
                    width: evidenceScroll.availableWidth
                    height: Math.max(evidenceScroll.availableHeight, implicitHeight)
                    leftPadding: 12; rightPadding: 24; topPadding: 10; bottomPadding: 10
                    text: section.modelData.text || "无记录"
                    readOnly: true; selectByMouse: true
                    color: theme.text; font.family: theme.family; font.pixelSize: theme.detailSize
                    wrapMode: TextEdit.Wrap; textFormat: TextEdit.PlainText
                    background: null
                }
            }
        }
    }
}
