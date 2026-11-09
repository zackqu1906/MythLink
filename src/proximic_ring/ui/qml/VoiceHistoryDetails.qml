import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

ColumnLayout {
    id: panel
    objectName: "voiceHistoryDetails"
    required property var details
    property bool screenshotExpanded: false
    spacing: 12
    VoiceHistoryTheme { id: theme }

    component DetailText: ScrollView {
        id: textScroll
        required property string contentText
        required property string fieldKey
        property int maximumHeight: 280
        objectName: "voiceHistoryDetailScroll_" + fieldKey
        Layout.fillWidth: true; Layout.minimumWidth: 0
        // Keep a clear lane for scrolling the surrounding history list.
        Layout.rightMargin: 40
        Layout.preferredHeight: Math.min(maximumHeight, Math.max(48, body.implicitHeight + topPadding + bottomPadding))
        clip: true; padding: 1
        contentWidth: availableWidth
        contentHeight: body.implicitHeight
        ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
        ScrollBar.vertical.policy: ScrollBar.AsNeeded
        background: Rectangle { color: theme.surface; border.color: theme.line; radius: 6 }
        TextArea {
            id: body
            objectName: "voiceHistoryDetail_" + textScroll.fieldKey
            width: textScroll.availableWidth
            height: Math.max(textScroll.availableHeight, implicitHeight)
            leftPadding: 12; rightPadding: 24; topPadding: 10; bottomPadding: 10
            text: textScroll.contentText
            readOnly: true; selectByMouse: true
            color: theme.text; font.family: theme.family; font.pixelSize: theme.detailSize
            wrapMode: TextEdit.Wrap; textFormat: TextEdit.PlainText
            background: null
        }
    }

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
    Repeater {
        model: panel.details.sections || []
        delegate: Rectangle {
            id: section
            required property var modelData
            property bool expanded: !modelData.collapsed
            property bool inputExpanded: false
            property bool alternativesExpanded: false
            Layout.fillWidth: true
            implicitHeight: sectionContent.implicitHeight + 28
            color: theme.subtle; radius: 8; border.color: theme.line
            ColumnLayout {
                id: sectionContent
                anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
                anchors.margins: 14; spacing: 8
                RowLayout {
                    Layout.fillWidth: true
                    Label {
                        Layout.fillWidth: true; text: section.modelData.title
                        color: theme.text; font.family: theme.family; font.pixelSize: theme.detailSize; font.weight: Font.DemiBold
                        wrapMode: Text.Wrap; textFormat: Text.PlainText
                    }
                    FigmaAction {
                        objectName: "voiceHistoryOutputToggle_" + section.modelData.key
                        visible: Boolean(section.modelData.collapsed)
                        text: section.expanded ? "收起" : "查看输出"; quiet: true
                        onClicked: section.expanded = !section.expanded
                    }
                }
                Label {
                    Layout.fillWidth: true; visible: Boolean(section.modelData.metaText)
                    text: section.modelData.metaText || ""
                    color: theme.muted; font.family: theme.family; font.pixelSize: 13
                    wrapMode: Text.Wrap; textFormat: Text.PlainText
                }
                Label {
                    Layout.fillWidth: true; visible: Boolean(section.modelData.description)
                    text: section.modelData.description || ""
                    color: theme.muted; font.family: theme.family; font.pixelSize: 13
                    wrapMode: Text.Wrap; textFormat: Text.PlainText
                }
                ColumnLayout {
                    Layout.fillWidth: true; spacing: 6; visible: section.expanded
                    Label { text: "输出"; color: theme.muted; font.family: theme.family; font.pixelSize: 13 }
                    DetailText {
                        fieldKey: section.modelData.key
                        contentText: section.modelData.text || "未记录输出"
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        FigmaAction {
                            objectName: "voiceHistoryInputToggle_" + section.modelData.key
                            visible: Boolean(section.modelData.inputText)
                            text: section.inputExpanded ? "收起输入" : "查看输入"; quiet: true
                            onClicked: section.inputExpanded = !section.inputExpanded
                        }
                        FigmaAction {
                            visible: Boolean(section.modelData.alternativesText)
                            text: section.alternativesExpanded ? "收起其他结果" : "查看其他结果"; quiet: true
                            onClicked: section.alternativesExpanded = !section.alternativesExpanded
                        }
                        Item { Layout.fillWidth: true }
                    }
                    DetailText {
                        visible: section.inputExpanded
                        fieldKey: section.modelData.key + "_input"
                        contentText: section.modelData.inputText || ""
                        maximumHeight: 200
                    }
                    DetailText {
                        visible: section.alternativesExpanded
                        fieldKey: section.modelData.key + "_alternatives"
                        contentText: section.modelData.alternativesText || ""
                        maximumHeight: 200
                    }
                }
            }
        }
    }
    FigmaAction {
        objectName: "voiceHistoryScreenshotToggle"
        text: panel.screenshotExpanded ? "收起截图" : "查看录音时截图"
        quiet: true; visible: Boolean(panel.details.screenshotUrl)
        onClicked: panel.screenshotExpanded = !panel.screenshotExpanded
    }
    Image {
        objectName: "voiceHistoryScreenshot"
        Layout.fillWidth: true; Layout.preferredHeight: visible ? 260 : 0
        visible: panel.screenshotExpanded && Boolean(panel.details.screenshotUrl)
        source: panel.details.screenshotUrl || ""
        fillMode: Image.PreserveAspectFit; asynchronous: true; cache: false
    }
    Label {
        Layout.fillWidth: true; visible: panel.screenshotExpanded
        text: panel.details.screenshotText || ""
        color: theme.muted; font.family: theme.family; font.pixelSize: 13
        wrapMode: Text.Wrap; textFormat: Text.PlainText
    }
    FigmaAction {
        text: "打开原图"; quiet: true; visible: panel.screenshotExpanded && Boolean(panel.details.screenshotUrl)
        onClicked: Qt.openUrlExternally(panel.details.screenshotUrl)
    }
}
