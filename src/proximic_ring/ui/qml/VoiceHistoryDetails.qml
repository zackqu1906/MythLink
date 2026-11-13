import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

ColumnLayout {
    id: panel
    objectName: "voiceHistoryDetails"
    required property var details
    spacing: 12
    VoiceHistoryTheme { id: theme }

    // Standalone text grows with its complete contents. With no inner Flickable,
    // mouse-wheel and trackpad events reach the surrounding history list.
    component DetailText: TextArea {
        id: body
        required property string contentText
        required property string fieldKey
        property bool warning: false
        objectName: visible ? "voiceHistoryDetail_" + fieldKey : ""
        Layout.fillWidth: true; Layout.minimumWidth: 0
        implicitHeight: Math.max(40, contentHeight + topPadding + bottomPadding)
        leftPadding: 12; rightPadding: 12; topPadding: 10; bottomPadding: 10
        text: contentText
        readOnly: true; selectByMouse: true
        color: warning ? theme.warning : theme.text
        font.family: theme.family; font.pixelSize: theme.detailSize
        wrapMode: TextEdit.Wrap; textFormat: TextEdit.PlainText
        background: Rectangle {
            color: body.warning ? theme.warningSurface : theme.surface
            border.color: body.warning ? theme.warningBorder : theme.line
            radius: 6
        }
    }

    Rectangle { Layout.fillWidth: true; implicitHeight: 1; color: theme.line }
    Label {
        Layout.fillWidth: true; visible: Boolean(panel.details.error)
        text: panel.details.error || ""
        color: theme.warning; font.family: theme.family; font.pixelSize: theme.bodySize
        wrapMode: Text.Wrap; textFormat: Text.PlainText
    }
    Repeater {
        model: panel.details.sections || []
        delegate: Rectangle {
            id: section
            required property var modelData
            objectName: "voiceHistorySection_" + modelData.key
            Layout.fillWidth: true; Layout.minimumWidth: 0
            implicitHeight: sectionContent.implicitHeight + 28
            color: theme.subtle; radius: 8; border.color: theme.line
            ColumnLayout {
                id: sectionContent
                anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
                anchors.margins: 14; spacing: 10
                RowLayout {
                    Layout.fillWidth: true
                    Label {
                        Layout.fillWidth: true; text: section.modelData.title
                        color: theme.text; font.family: theme.family; font.pixelSize: theme.bodySize; font.weight: Font.DemiBold
                        wrapMode: Text.Wrap; textFormat: Text.PlainText
                    }
                    FigmaAction {
                        text: "打开原图"; quiet: true; visible: Boolean(section.modelData.imageUrl)
                        onClicked: Qt.openUrlExternally(section.modelData.imageUrl)
                    }
                }
                Label {
                    Layout.fillWidth: true; visible: Boolean(section.modelData.statusText)
                    text: section.modelData.statusText || ""
                    color: theme.muted; font.family: theme.family; font.pixelSize: 13
                    wrapMode: Text.Wrap; textFormat: Text.PlainText
                }
                Label {
                    objectName: "voiceHistoryDetailsWarning"
                    Layout.fillWidth: true; visible: Boolean(section.modelData.warningText)
                    text: section.modelData.warningText || ""; padding: 10
                    color: theme.warning; font.family: theme.family; font.pixelSize: theme.detailSize
                    wrapMode: Text.Wrap; textFormat: Text.PlainText
                    background: Rectangle { color: theme.warningSurface; border.color: theme.warningBorder; radius: 6 }
                }
                Repeater {
                    model: section.modelData.items || []
                    delegate: ColumnLayout {
                        id: result
                        required property var modelData
                        Layout.fillWidth: true; Layout.minimumWidth: 0; spacing: 6
                        Label {
                            Layout.fillWidth: true; visible: Boolean(result.modelData.title)
                            text: result.modelData.title || ""; font.weight: Font.DemiBold
                            color: theme.text; font.family: theme.family; font.pixelSize: theme.detailSize
                        }
                        Label {
                            Layout.fillWidth: true; visible: Boolean(result.modelData.metaText)
                            text: result.modelData.metaText || ""
                            color: theme.muted; font.family: theme.family; font.pixelSize: 13
                            wrapMode: Text.Wrap; textFormat: Text.PlainText
                        }
                        Label {
                            objectName: visible ? "voiceHistoryClassification_" + result.modelData.key : ""
                            Layout.fillWidth: true; visible: Boolean(result.modelData.classificationText)
                            text: result.modelData.classificationText ? "编辑分类：" + result.modelData.classificationText : ""
                            color: theme.edit; font.family: theme.family; font.pixelSize: theme.bodySize
                            font.weight: Font.DemiBold; wrapMode: Text.Wrap; textFormat: Text.PlainText
                        }
                        Label {
                            objectName: visible ? "voiceHistoryClassificationNote_" + result.modelData.key : ""
                            Layout.fillWidth: true; visible: Boolean(result.modelData.classificationNote)
                            text: result.modelData.classificationNote || ""
                            color: theme.muted; font.family: theme.family; font.pixelSize: 13
                            wrapMode: Text.Wrap; textFormat: Text.PlainText
                        }
                        Label {
                            Layout.fillWidth: true; visible: Boolean(result.modelData.description)
                            text: result.modelData.description || ""
                            color: theme.muted; font.family: theme.family; font.pixelSize: 13
                            wrapMode: Text.Wrap; textFormat: Text.PlainText
                        }
                        DetailText {
                            visible: result.modelData.outputVisible !== false && Boolean(result.modelData.text)
                            fieldKey: result.modelData.key
                            contentText: result.modelData.text || ""
                        }
                        Label {
                            visible: Boolean(result.modelData.errorText)
                            text: "失败原因"; color: theme.warning; font.family: theme.family
                            font.pixelSize: theme.detailSize; font.weight: Font.DemiBold
                        }
                        DetailText {
                            visible: Boolean(result.modelData.errorText)
                            fieldKey: result.modelData.key + "_error"; warning: true
                            contentText: result.modelData.errorText || ""
                        }
                        DetailText {
                            visible: Boolean(result.modelData.alternativesText)
                            fieldKey: result.modelData.key + "_alternatives"
                            contentText: result.modelData.alternativesText || ""
                        }
                    }
                }
                ColumnLayout {
                    Layout.fillWidth: true; spacing: 6
                    visible: Boolean(section.modelData.errorText)
                    Label {
                        text: "失败原因"; color: theme.warning; font.family: theme.family
                        font.pixelSize: theme.detailSize; font.weight: Font.DemiBold
                    }
                    DetailText {
                        fieldKey: "outcomeError"; warning: true
                        contentText: section.modelData.errorText || ""
                    }
                }
                Image {
                    objectName: section.modelData.kind === "image" ? "voiceHistoryScreenshot" : ""
                    Layout.fillWidth: true
                    Layout.preferredHeight: visible ? (sourceSize.width > 0 ? width * sourceSize.height / sourceSize.width : 180) : 0
                    visible: Boolean(section.modelData.imageUrl)
                    source: section.modelData.imageUrl || ""
                    fillMode: Image.PreserveAspectFit; asynchronous: true; cache: false
                }
                Label {
                    Layout.fillWidth: true; visible: section.modelData.kind === "image"
                    text: section.modelData.caption || ""
                    color: theme.muted; font.family: theme.family; font.pixelSize: 13
                    wrapMode: Text.Wrap; textFormat: Text.PlainText
                }
            }
        }
    }
}
