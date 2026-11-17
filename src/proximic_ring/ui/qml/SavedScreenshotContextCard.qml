import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Rectangle {
    id: card
    required property var capture
    readonly property var record: capture.contextRecord
    property bool expanded: true
    objectName: "savedScreenshotContextCard"
    implicitHeight: content.implicitHeight + 28
    color: theme.surface; radius: 12; border.color: theme.line
    FigmaTheme { id: theme }
    ColumnLayout {
        id: content
        anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
        anchors.margins: 14; spacing: 10
        RowLayout {
            Layout.fillWidth: true
            Label {
                Layout.fillWidth: true
                text: "当前保存的上下文"
                color: theme.text; font.pixelSize: 16; font.weight: Font.DemiBold
            }
            FigmaAction {
                objectName: "savedScreenshotExpand"
                text: card.expanded ? "收起" : "展开"; quiet: true
                onClicked: card.expanded = !card.expanded
            }
        }
        Label {
            Layout.fillWidth: true
            text: "仅保留最近一次截图或复制文本；编辑时提到“刚才的”或“保存的”等内容，会参考这份文字。"
            wrapMode: Text.Wrap; color: theme.muted; font.pixelSize: 13
        }
        Label {
            objectName: "savedScreenshotStatus"
            Layout.fillWidth: true
            text: card.record.status === "processing" ? "正在识别并清理截图文字…"
                : card.record.status === "ok" ? (card.record.source === "copied_text" ? "复制文本 · " : "截图文字 · ") + Qt.formatDateTime(new Date(card.record.captured_at), "MM-dd hh:mm:ss")
                : (card.record.reason || "尚未保存截图或复制文本")
            wrapMode: Text.Wrap; textFormat: Text.PlainText
            color: card.record.status === "unavailable" ? theme.warning : theme.muted
            font.pixelSize: 13
        }
        RowLayout {
            visible: card.expanded && Boolean(card.record.capture_id)
            Layout.fillWidth: true; spacing: 14
            Image {
                objectName: "savedScreenshotImage"
                visible: Boolean(card.record.imageUrl)
                Layout.preferredWidth: Math.min(240, card.width * 0.35)
                Layout.preferredHeight: 150
                source: card.record.imageUrl || ""
                fillMode: Image.PreserveAspectFit
                asynchronous: true
            }
            ScrollView {
                Layout.fillWidth: true; Layout.preferredHeight: 150
                clip: true
                TextArea {
                    objectName: "savedScreenshotCleanedText"
                    text: card.record.text || (card.record.status === "processing" ? "等待文字处理完成…" : "暂无可用文字")
                    readOnly: true; selectByMouse: true
                    wrapMode: TextEdit.Wrap; textFormat: TextEdit.PlainText
                    leftPadding: 12; rightPadding: 12; topPadding: 10; bottomPadding: 10
                    topInset: 0; bottomInset: 0
                    color: theme.text; font.pixelSize: 14
                    background: Rectangle { color: theme.surface; border.color: theme.line; radius: 6 }
                }
            }
        }
        FigmaAction {
            visible: card.expanded && Boolean(card.record.imageUrl)
            text: "打开保存的截图"; quiet: true
            onClicked: Qt.openUrlExternally(card.record.imageUrl)
        }
    }
}
